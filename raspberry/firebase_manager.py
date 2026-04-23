import logging
import asyncio
import json
from typing import Dict, Any, Optional, Callable
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from notification_service import NotificationService
from control_access_manager import ControlAccessManager
import aiohttp

import config

logger = logging.getLogger(__name__)

class FirebaseManager:
    def __init__(self, robot_instance=None):
        self.initialized = False
        self.robot_id = config.ROBOT_ID
        self.db: Optional[firestore.Client] = None
        self.doc_ref = None
        self.robot = robot_instance
        self.notification_service = None
        self._handling_offer = False
        
        # Gestão de Acesso e Fila
        self.access_manager = ControlAccessManager()
        self.current_controller = None

        # WebRTC
        self.pc: Optional[RTCPeerConnection] = None
        self.loop = asyncio.get_event_loop()
        self.on_control_change: Optional[Callable] = None
        self.connected = False
        self._snapshot_listener = None
        self._processed_app_candidates = set()
        self._pending_candidates = []
        self._remote_description_set = False

    def initialize(self) -> bool:
        """Inicializa a ligação ao Firebase e limpa estados residuais."""
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(config.FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)

            self.db = firestore.client()
            self.doc_ref = self.db.collection('robots').document(self.robot_id)

            logger.info("Limpando sessões WebRTC e reiniciando fila de controlo...")
            
            # Reset total no arranque do Robô
            self.doc_ref.update({
                'webrtc_session': None,
                'app_candidates': [],
                'robot_candidates': [],
                'status.online': True,
                'status.video_client_count': 0,
                'status.last_boot': firestore.SERVER_TIMESTAMP,
                'control.active_controller_email': None,
                'control.viewer_queue': [],
                'control.last_access': firestore.SERVER_TIMESTAMP
            })

            self.notification_service = NotificationService(self.db, self.robot_id)
            self._start_firestore_listener()
            
            # Iniciar monitor de timeout de controlo
            asyncio.run_coroutine_threadsafe(self._control_timeout_loop(), self.loop)

            self.connected = True
            self.initialized = True
            logger.info("✓ Firebase Manager inicializado com sucesso.")
            return True
        except Exception as e:
            logger.error(f"✗ Falha crítica no Firebase: {e}")
            return False

    def _start_firestore_listener(self):
        """Monitoriza o Firestore para Handshake WebRTC e Fila de Controlo."""
        def on_snapshot(doc_snapshot, changes, read_time):
            for doc in doc_snapshot:
                data = doc.to_dict()
                if not data: continue

                # 1. TRATAMENTO DE HANDSHAKE (OFFER)
                session = data.get('webrtc_session')
                control_data = data.get('control', {})
                # A App deve enviar este campo para sabermos quem colocar na fila
                app_email = control_data.get('last_handshake_email')

                if session and session.get('offer') and not session.get('answer'):
                    if not self._handling_offer:
                        self._handling_offer = True
                        
                        # Tentar registar na fila de controlo
                        if app_email:
                            self.access_manager.request_control(app_email)
                            self._sync_control_state()

                        logger.info(f"📡 Oferta WebRTC de {app_email}. Iniciando conexão...")
                        asyncio.run_coroutine_threadsafe(
                            self._handle_webrtc_offer(session['offer']),
                            self.loop
                        )

                # 2. CANDIDATOS ICE
                app_candidates = data.get('app_candidates', [])
                if app_candidates and self.pc and self._remote_description_set:
                    for cand_data in app_candidates:
                        cand_str = cand_data.get('candidate')
                        if cand_str and cand_str not in self._processed_app_candidates:
                            self._processed_app_candidates.add(cand_str)
                            asyncio.run_coroutine_threadsafe(
                                self._add_ice_candidate(cand_data),
                                self.loop
                            )

        self._snapshot_listener = self.doc_ref.on_snapshot(on_snapshot)

    async def _control_timeout_loop(self):
        """Verifica periodicamente se o controlador atual expirou."""
        while True:
            await asyncio.sleep(5)
            if self.current_controller:
                status = self.access_manager.get_control_status()
                if not status['is_controlled']:
                    logger.warning(f"Timeout: {self.current_controller} perdeu o controlo.")
                    await self._promote_next_controller()

    async def _promote_next_controller(self):
        """Liberta o controlo atual e passa para o próximo na fila."""
        if self.current_controller:
            self.access_manager.release_control(self.current_controller)
        
        status = self.access_manager.get_control_status()
        next_user = status['current_controller']
        self.current_controller = next_user
        
        self._sync_control_state()
        
        if self.on_control_change:
            self.on_control_change(next_user, next_user is not None)

    def _sync_control_state(self):
        """Sincroniza o estado do AccessManager com o Firestore."""
        self.doc_ref.update({
            'control.active_controller_email': self.access_manager.current_controller,
            'control.viewer_queue': self.access_manager.control_queue,
            'status.video_client_count': len(self.access_manager.control_queue) + (1 if self.access_manager.current_controller else 0)
        })

    async def _wait_for_stream_ready(self, path="robot", timeout=20) -> bool:
        url = "http://127.0.0.1:9997/v3/paths/list"
        async with aiohttp.ClientSession() as session:
            for i in range(timeout // 2):
                try:
                    async with session.get(url, timeout=2) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for item in data.get("items", []):
                                if item.get("name") == path and item.get("ready"):
                                    return True
                except: pass
                await asyncio.sleep(2)
        return False

    async def _handle_webrtc_offer(self, offer_data):
        ice_servers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]
        
        if self.pc:
            await self.pc.close()

        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        self._remote_description_set = False

        @self.pc.on("iceconnectionstatechange")
        async def on_ice_connection_state():
            if self.pc:
                state = self.pc.iceConnectionState
                logger.info(f"ICE Connection State: {state}")
                if state in ["failed", "closed", "disconnected"]:
                    await self._promote_next_controller()

        @self.pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate:
                self.doc_ref.update({
                    'robot_candidates': firestore.ArrayUnion([{
                        'candidate': candidate.candidate,
                        'sdpMid': candidate.sdpMid,
                        'sdpMLineIndex': candidate.sdpMLineIndex
                    }])
                })

        @self.pc.on("datachannel")
        def on_datachannel(channel):
            @channel.on("message")
            def on_message(message):
                if self.current_controller:
                    self.access_manager.update_activity(self.current_controller)
                    try:
                        cmd = json.loads(message)
                        if self.robot:
                            asyncio.run_coroutine_threadsafe(
                                self.robot.execute_command(cmd.get('x', 0), cmd.get('y', 0), self.current_controller),
                                self.loop
                            )
                    except Exception as e: logger.error(f"Erro comando: {e}")

        try:
            if not await self._wait_for_stream_ready():
                raise Exception("RTSP Stream Offline")

            player = MediaPlayer('rtsp://127.0.0.1:8554/robot', options={'rtsp_transport': 'tcp'})
            
            # Aguardar track de vídeo
            for _ in range(10):
                if player.video: break
                await asyncio.sleep(0.5)

            if player.video:
                self.pc.addTrack(player.video)
                logger.info("✓ Track de vídeo adicionada.")

            # Handshake
            try:
                answer = await self.pc.createAnswer()
                await self.pc.setLocalDescription(answer)
            except Exception as e:
                logger.error(f"Erro na negociação SDP: {e}")
                self._handling_offer = False
                return

            self.doc_ref.update({
                'webrtc_session.answer': {'sdp': self.pc.localDescription.sdp, 'type': self.pc.localDescription.type}
            })
            logger.info("✓ Answer enviada.")

        except Exception as e:
            logger.error(f"Erro Handshake: {e}")
        finally:
            self._handling_offer = False

    async def _add_ice_candidate(self, c):
        if not self.pc or not self._remote_description_set: return
        try:
            from aiortc.sdp import candidate_from_sdp
            parsed = candidate_from_sdp(c['candidate'].replace("candidate:", ""))
            parsed.sdpMid, parsed.sdpMLineIndex = c['sdpMid'], c['sdpMLineIndex']
            await self.pc.addIceCandidate(parsed)
        except Exception as e: logger.warning(f"ICE Ignorado: {e}")

    async def _flush_pending_candidates(self):
        for cand in self._pending_candidates:
            await self._add_ice_candidate(cand)
        self._pending_candidates.clear()

    async def save_telemetry(self, data: Dict[str, Any]):
        if self.initialized:
            self.doc_ref.set({'telemetry': data, 'status.last_update': firestore.SERVER_TIMESTAMP}, merge=True)

    async def disconnect(self):
        if self._snapshot_listener: self._snapshot_listener.unsubscribe()
        if self.pc: await self.pc.close()
        if self.initialized:
            self.doc_ref.update({'status.online': False, 'webrtc_session': None})