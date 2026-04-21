import logging
import asyncio
import json
from typing import Dict, Any, Optional, Callable
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.contrib.media import MediaPlayer
from notification_service import NotificationService

import config

logger = logging.getLogger(__name__)

class FirebaseManager:
    def __init__(self, robot_instance=None):
        self.initialized = False
        self.robot_id = config.ROBOT_ID
        self.db = None
        self.doc_ref = None
        self.robot = robot_instance
        self.notification_service = None
        
        self.pcs = set()
        self.loop = asyncio.get_event_loop()
        self.on_control_change: Optional[Callable] = None
        self.current_controller = None
        self.connected = False
        self._snapshot_listener = None

    async def initialize(self) -> bool:
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(config.FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)
            
            self.db = firestore.client()
            self.doc_ref = self.db.collection('robots').document(self.robot_id)
            
            # --- LIMPEZA DE SESSÕES ANTIGAS ---
            logger.info("Limpando sessões WebRTC antigas e candidatos...")
            self.doc_ref.update({
                'webrtc_session': None,
                'app_candidates': [],
                'robot_candidates': []
            })
            # ----------------------------------

            self.notification_service = NotificationService(self.db, self.robot_id)
            
            self._start_firestore_listener()
            
            self.notification_service.broadcast_alert(
                "Sistema Online", 
                "O AgroMotion iniciou com sucesso e está pronto a operar.", 
                "success"
            )
            
            self.connected = True
            self.initialized = True
            return True
        except Exception as e:
            logger.error(f"✗ Firebase init failed: {e}")
            return False

    def _start_firestore_listener(self):
        def on_snapshot(doc_snapshot, changes, read_time):
            for doc in doc_snapshot:
                data = doc.to_dict()
                if not data: continue

                # WebRTC Handshake
                session = data.get('webrtc_session')
                if session and session.get('offer') and not session.get('answer'):
                    logger.info("Offer detected. Generating P2P Answer...")
                    asyncio.run_coroutine_threadsafe(
                        self._handle_webrtc_offer(session['offer'], data.get('app_candidates', [])), 
                        self.loop
                    )

                # Control Lock & Notifications
                control = data.get('control', {})
                new_controller = control.get('current_controller')
                if new_controller != self.current_controller:
                    if new_controller:
                        self.notification_service.broadcast_alert(
                            "Controlo Remoto", 
                            f"O utilizador {new_controller} assumiu o comando do robô.", 
                            "info"
                        )
                    self.current_controller = new_controller
                    if self.on_control_change:
                        self.on_control_change(new_controller, new_controller is not None)

        self._snapshot_listener = self.doc_ref.on_snapshot(on_snapshot)

    async def _handle_webrtc_offer(self, offer_data, app_candidates):
        # Configuração TURN/STUN para acesso global
        ice_servers = [
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(
                urls=["turn:openrelay.metered.ca:80"],
                username="openrelayproject",
                credential="openrelayproject"
            ),
            RTCIceServer(
                urls=["turn:openrelay.metered.ca:443"],
                username="openrelayproject",
                credential="openrelayproject"
            )
        ]
        
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        self.pcs.add(pc)

        # Handler do DataChannel para comandos
        @pc.on("datachannel")
        def on_datachannel(channel):
            @channel.on("message")
            def on_message(message):
                try:
                    cmd = json.loads(message)
                    if self.robot:
                        asyncio.run_coroutine_threadsafe(
                            self.robot.execute_command(cmd.get('x', 0), cmd.get('y', 0), self.current_controller),
                            self.loop
                        )
                except: pass

        try:
            # Captura o vídeo do MediaMTX (que já vimos funcionar no browser)
            player = MediaPlayer('rtsp://127.0.0.1:8554/robot', options={
                'rtsp_transport': 'tcp',
                'stimeout': '5000000'
            })
            if player.video:
                pc.addTrack(player.video)
            
            # Define a oferta remota
            await pc.setRemoteDescription(RTCSessionDescription(offer_data['sdp'], offer_data['type']))
            
            # Adiciona os candidatos da App (CORREÇÃO DE ARGUMENTOS)
            if app_candidates:
                for c in app_candidates:
                    try:
                        # Passamos apenas os valores por ordem: candidate, sdpMid, sdpMLineIndex
                        cand = RTCIceCandidate(
                            str(c.get('candidate')), 
                            str(c.get('sdpMid', '0')), 
                            int(c.get('sdpMLineIndex', 0))
                        )
                        await pc.addIceCandidate(cand)
                    except Exception as ice_err:
                        logger.warning(f"Candidato ignorado: {ice_err}")
            
            # Gera e envia a resposta
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            
            self.doc_ref.update({
                'webrtc_session.answer': {
                    'sdp': pc.localDescription.sdp, 
                    'type': pc.localDescription.type
                }
            })
            logger.info("✓ Resposta WebRTC enviada com sucesso.")
            
        except Exception as e:
            logger.error(f"Erro no Handshake: {e}")

    async def save_telemetry(self, data: Dict[str, Any]):
        if self.initialized:
            self.doc_ref.set({
                'telemetry': data,
                'status': {'online': True, 'last_update': firestore.SERVER_TIMESTAMP}
            }, merge=True)

            try:
                self.doc_ref.collection('telemetry_history').add(data)
            except Exception as e:
                logger.error(f"Erro ao gravar histórico: {e}")

    async def disconnect(self):
        if self._snapshot_listener: self._snapshot_listener.unsubscribe()
        for pc in list(self.pcs): await pc.close()
        if self.initialized: self.doc_ref.update({'status.online': False})

    async def acquire_control_lock(self, user_email: str):
        self.doc_ref.update({
            'control.current_controller': user_email, 
            'control.lock_time': firestore.SERVER_TIMESTAMP
        })

    async def release_control_lock(self, user_email: str):
        self.doc_ref.update({
            'control.current_controller': None, 
            'control.lock_time': None
        })

    async def health_check(self) -> Dict[str, Any]:
        try:
            doc = self.doc_ref.get()
            return {"connected": doc.exists, "timestamp": datetime.now().isoformat()}
        except Exception as e:
            return {"connected": False, "error": str(e)}