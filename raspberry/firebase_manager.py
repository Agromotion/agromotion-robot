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
                if not data:
                    continue

                control_data = data.get('control', {})
                app_email = control_data.get('last_handshake_email')
                session = data.get('webrtc_session')

                # 1. TRATAMENTO DE HANDSHAKE (OFFER)
                if session and session.get('offer') and not session.get('answer'):
                    if self.pc and self.pc.iceConnectionState in ["checking", "connected", "completed"]:
                        logger.warning("🚫 Ignorando nova offer - handshake em progresso")
                        continue

                    if not self._handling_offer:
                        self._handling_offer = True
                        if app_email:
                            logger.info(f"⚙️ Solicitando controlo e bloqueando timeout para: {app_email}")
                            self.access_manager.request_control(app_email)
                            self.access_manager.update_activity(app_email)  # Evita timeout imediato
                            self.current_controller = self.access_manager.current_controller
                            self._sync_control_state()
                        
                        logger.info(f"📡 Oferta WebRTC de {app_email}. Iniciando conexão...")
                        asyncio.run_coroutine_threadsafe(
                            self._handle_webrtc_offer(session['offer']),
                            self.loop
                        )
                        continue  # ✨ Alterado de return para continue (Corrige o crash de inicialização)

                # 2. PROTEÇÃO DE SESSÃO ATIVA 
                if self.pc and self.pc.iceConnectionState in ["checking", "completed", "connected"]:
                    if not app_email or app_email == 'unknown':
                        logger.debug("Snapshot de limpeza ignorado para proteger Handshake ativo.")
                        continue

                # 3. CANDIDATOS ICE
                app_candidates = data.get('app_candidates', [])
                if app_candidates and self.pc:
                    for cand_data in app_candidates:
                        cand_str = cand_data.get('candidate')
                        if cand_str and cand_str not in self._processed_app_candidates:
                            self._processed_app_candidates.add(cand_str)
                            if self._remote_description_set:
                                asyncio.run_coroutine_threadsafe(
                                    self._add_ice_candidate(cand_data),
                                    self.loop
                                )
                            else:
                                self._pending_candidates.append(cand_data)

        self._snapshot_listener = self.doc_ref.on_snapshot(on_snapshot)

    async def _control_timeout_loop(self):
            """Verifica periodicamente se o controlador atual expirou."""
            while True:
                await asyncio.sleep(2)
                if self.current_controller:
                    # 🛑 SÓ verifica inatividade se a ligação NÃO estiver em negociação/handshake
                    if self.pc and self.pc.iceConnectionState in ["checking"]:
                        self.access_manager.update_activity(self.current_controller)
                        continue

                    # Se a ligação falhou ou está ligada mas o joystick parou de enviar dados há > 7s
                    if self.access_manager.is_inactive(timeout_seconds=20):
                        logger.warning(f"Detetada perda de sinal de {self.current_controller}. A libertar...")
                        if self.pc:
                            try:
                                self.pc.on("iceconnectionstatechange", None)
                                self.pc.on("icecandidate", None)
                                self.pc.on("datachannel", None)
                                await self.pc.close()
                            except Exception:
                                pass
                            self.pc = None
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
        """Verifica via API do MediaMTX se o stream está activo."""
        url = "http://127.0.0.1:9997/v3/paths/list"
        async with aiohttp.ClientSession() as session:
            for i in range(timeout // 2):
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for item in data.get("items", []):
                                if item.get("name") == path:
                                    # Compatibilidade com diferentes versões do MediaMTX
                                    is_ready = (
                                        item.get("ready") is True or
                                        item.get("readyTime") is not None or
                                        item.get("bytesReceived", 0) > 0 or
                                        len(item.get("tracks", [])) > 0
                                    )
                                    if is_ready:
                                        logger.info(f"✓ Stream '{path}' confirmado (tentativa {i+1})")
                                        return True
                                    logger.warning(f"Path '{path}' existe mas não está ready ainda.")
                except Exception as e:
                    logger.debug(f"MediaMTX API: {e}")
                await asyncio.sleep(2)
        return False

    async def _handle_webrtc_offer(self, offer_data):
        ice_servers = [
        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
        RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),

        # OpenRelay STUN
        RTCIceServer(urls=["stun:openrelay.metered.ca:80"]),

        # OpenRelay TURN (TCP + UDP fallback)
        RTCIceServer(
            urls=[
                "turn:openrelay.metered.ca:80",
                "turn:openrelay.metered.ca:443",
                "turn:openrelay.metered.ca:443?transport=tcp"
            ],
            username="openrelayproject",
            credential="openrelayproject"
        )
    ]

        # Reset de estado para nova sessão
        self._remote_description_set = False
        self._pending_candidates.clear()
        self._processed_app_candidates.clear()

        # FECHO SEGURO: Evita o crash de AttributeError / 'NoneType' object has no attribute 'sendto'
        if self.pc:
            logger.info("Fechando conexão WebRTC anterior de forma segura...")
            try:
                # Remove os handlers para que nenhum evento tente disparar durante o fecho
                self.pc.on("iceconnectionstatechange", None)
                self.pc.on("icecandidate", None)
                self.pc.on("datachannel", None)
                
                await self.pc.close()
            except Exception as e:
                logger.debug(f"Erro esperado ao fechar PC: {e}")
            finally:
                self.pc = None
                # Pequena pausa para deixar o loop do asyncio processar os cancelamentos do aioice
                await asyncio.sleep(0.2) 

        try:
            # 1. Verificar stream RTSP
            if not await self._wait_for_stream_ready():
                logger.error("Abortando handshake: stream RTSP não disponível.")
                return

            # 2. Criar PeerConnection
            self.pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))

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
                    logger.debug(f"Candidato ICE do Robô: {candidate.candidate[:60]}...")
                    self.doc_ref.update({
                        'robot_candidates': firestore.ArrayUnion([{
                            'candidate': candidate.candidate,
                            'sdpMid': candidate.sdpMid,
                            'sdpMLineIndex': candidate.sdpMLineIndex
                        }])
                    })

            @self.pc.on("datachannel")
            def on_datachannel(channel):
                logger.info(f"DataChannel '{channel.label}' estabelecido!")

                @channel.on("message")
                def on_message(message):
                    if self.current_controller:
                        self.access_manager.update_activity(self.current_controller)

                    try:
                        cmd = json.loads(message)

                        x = cmd.get('x', None)
                        y = cmd.get('y', None)
                        drum = cmd.get('drum', None)

                        # 🚀 ENVIO "FIRE-AND-FORGET" PARA O ROBOT OBJECT
                        if self.robot:
                            if drum is not None:
                                # NÃO executar diretamente (evita concorrência)
                                asyncio.run_coroutine_threadsafe(
                                    self.robot.execute_drum(drum, self.current_controller),
                                    self.loop
                                )

                            elif x is not None and y is not None:
                                asyncio.run_coroutine_threadsafe(
                                    self.robot.execute_command(x, y, self.current_controller),
                                    self.loop
                                )

                    except Exception as e:
                        logger.error(f"Erro ao processar mensagem do DataChannel: {e}")
            # 3. Adicionar track de vídeo
            options = {
                'rtsp_transport': 'tcp',
                'fflags': 'nobuffer+discardcorrupt',
                'flags': 'low_delay',
                'stimeout': '5000000',
            }
            player = MediaPlayer('rtsp://127.0.0.1:8554/robot', options=options)

            video_track = None
            for _ in range(10):
                if player.video is not None:
                    video_track = player.video
                    break
                await asyncio.sleep(0.5)

            if video_track is None:
                logger.error("MediaPlayer não expôs track de vídeo. Abortando.")
                return

            self.pc.addTrack(video_track)

            await self.pc.setRemoteDescription(
                RTCSessionDescription(sdp=offer_data['sdp'], type=offer_data['type'])
            )
            self._remote_description_set = True

            # 5. Processar candidatos ICE que chegaram enquanto esperávamos
            await self._flush_pending_candidates()

            # 6. Criar Answer
            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            logger.info("✓ Local description (answer) criada.")

            # 7. Publicar Answer no Firestore
            self.doc_ref.update({
                'webrtc_session.answer': {
                    'sdp': self.pc.localDescription.sdp,
                    'type': self.pc.localDescription.type
                }
            })
            logger.info("✓ Answer publicada no Firestore. Aguardando ICE...")

        except Exception as e:
            logger.error(f"Erro fatal no Handshake WebRTC: {e}", exc_info=True)
        finally:
            self._handling_offer = False

    async def _add_ice_candidate(self, c):
        """Injeta candidatos ICE da App no PeerConnection do Robô."""
        if not self.pc or not self._remote_description_set:
            return
        try:
            from aiortc.sdp import candidate_from_sdp
            candidate_str = str(c.get('candidate', ''))
            if not candidate_str:
                return
            parsed = candidate_from_sdp(candidate_str.replace("candidate:", ""))
            parsed.sdpMid = str(c.get('sdpMid', '0'))
            parsed.sdpMLineIndex = int(c.get('sdpMLineIndex', 0))
            await self.pc.addIceCandidate(parsed)
            logger.debug(f"✓ Candidato ICE da App injetado: {candidate_str[:60]}...")
        except Exception as e:
            logger.warning(f"Candidato ICE ignorado: {e}")

    async def _flush_pending_candidates(self):
        """Injeta candidatos que chegaram antes do setRemoteDescription."""
        if not self._pending_candidates:
            return
        logger.info(f"A processar {len(self._pending_candidates)} candidatos em fila...")
        for cand in self._pending_candidates:
            await self._add_ice_candidate(cand)
        self._pending_candidates.clear()

    async def save_telemetry(self, data: Dict[str, Any], save_history: bool = False):
        if not self.initialized:
            return
        try:
            self.doc_ref.set(
                {'telemetry': data},
                merge=True
            )

            if save_history:
                logger.info("Tentando gravar histórico...")
                self.doc_ref.collection('telemetry_history').add({
                    **data
                })
                logger.info("✅ Histórico gravado com sucesso!")

        except Exception as e:
            logger.error(f"Erro ao gravar telemetria: {e}")

    def start_listening(self):
        """Inicia a escuta de comandos. Só deve ser chamado quando o vídeo estiver OK."""
        if not self._snapshot_listener:
            logger.info("👂 Robô agora está a ouvir pedidos de conexão (Signaling ativo).")
            self._start_firestore_listener()
            asyncio.run_coroutine_threadsafe(self._control_timeout_loop(), self.loop)
            # Atualiza o Firestore para dizer às Apps que já podem enviar Offers
            self.doc_ref.update({'status.video_ready': True})

    async def disconnect(self):
        """Fecha todas as conexões de forma limpa."""
        logger.info("Encerrando Firebase Manager...")
        if self._snapshot_listener:
            self._snapshot_listener.unsubscribe()
        if self.pc:
            await self.pc.close()
        if self.initialized:
            self.doc_ref.update({
                'status.online': False,
                'status.video_ready': False, # IMPORTANTE
                'webrtc_session': None
            })

    async def acquire_control_lock(self, user_email: str):
        self.doc_ref.update({
            'control.current_controller': user_email,
            'control.lock_time': firestore.SERVER_TIMESTAMP
        })

    async def release_control_lock(self):
        self.doc_ref.update({
            'control.current_controller': None,
            'control.lock_time': None
        })

    async def health_check(self) -> Dict[str, Any]:
        try:
            doc = self.doc_ref.get()
            return {
                "connected": doc.exists,
                "timestamp": datetime.now().isoformat(),
                "webrtc_active": self.pc is not None and self.pc.iceConnectionState == "completed"
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}
