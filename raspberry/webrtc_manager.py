import logging
import asyncio
import json
import aiohttp
from typing import Optional, Callable
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from firebase_admin import firestore

logger = logging.getLogger(__name__)

class WebRTCManager:
    def __init__(self, doc_ref, loop, access_manager, robot_instance):
        self.doc_ref = doc_ref
        self.loop = loop
        self.access_manager = access_manager
        self.robot = robot_instance
        
        self.pc: Optional[RTCPeerConnection] = None
        self.handling_offer = False
        self.on_disconnect: Optional[Callable] = None
        
        self._processed_app_candidates = set()
        self._pending_candidates = []
        self._remote_description_set = False

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
                                    is_ready = item.get("ready") is True or item.get("bytesReceived", 0) > 0
                                    if is_ready:
                                        logger.info(f"✓ Stream '{path}' confirmado (tentativa {i+1})")
                                        return True
                except Exception as e:
                    logger.debug(f"MediaMTX API: {e}")
                await asyncio.sleep(2)
        return False

    async def handle_offer(self, offer_data):
        self.handling_offer = True
        ice_servers = [
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            RTCIceServer(urls=["stun:openrelay.metered.ca:80"]),
            RTCIceServer(
                urls=["turn:openrelay.metered.ca:80", "turn:openrelay.metered.ca:443", "turn:openrelay.metered.ca:443?transport=tcp"],
                username="openrelayproject", credential="openrelayproject"
            )
        ]

        self._remote_description_set = False
        self._pending_candidates.clear()
        self._processed_app_candidates.clear()

        if self.pc:
            logger.info("Fechando conexão WebRTC anterior de forma segura...")
            await self.close()
            await asyncio.sleep(0.2)

        try:
            if not await self._wait_for_stream_ready():
                logger.error("Abortando handshake: stream RTSP não disponível.")
                return

            self.pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))

            @self.pc.on("iceconnectionstatechange")
            async def on_ice_connection_state():
                pc_ref = getattr(self, "pc", None)
                if pc_ref:
                    state = pc_ref.iceConnectionState
                    logger.info(f"Estado de ligação ICE: {state}")
                    if state in ["failed", "closed", "disconnected"]:
                        await self.close()
                        if self.on_disconnect: 
                            if asyncio.iscoroutinefunction(self.on_disconnect):
                                await self.on_disconnect()
                            else:
                                self.on_disconnect()

            @self.pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    def _send_candidate():
                        try:
                            self.doc_ref.update({
                                'robot_candidates': firestore.ArrayUnion([{
                                    'candidate': candidate.candidate, 'sdpMid': candidate.sdpMid, 'sdpMLineIndex': candidate.sdpMLineIndex
                                }])
                            })
                        except Exception as e:
                            logger.error(f"Erro ao enviar candidato ICE: {e}")
                    self.loop.run_in_executor(None, _send_candidate)

            @self.pc.on("datachannel")
            def on_datachannel(channel):
                logger.info(f"DataChannel '{channel.label}' estabelecido!")
                @channel.on("message")
                def on_message(message):
                    if self.access_manager.current_controller:
                        self.access_manager.update_activity(self.access_manager.current_controller)
                    try:
                        cmd = json.loads(message)
                        if self.robot:
                            if cmd.get('drum') is not None:
                                asyncio.run_coroutine_threadsafe(self.robot.execute_drum(cmd['drum'], self.access_manager.current_controller), self.loop)
                            elif cmd.get('x') is not None and cmd.get('y') is not None:
                                asyncio.run_coroutine_threadsafe(self.robot.execute_command(cmd['x'], cmd['y'], self.access_manager.current_controller), self.loop)
                    except Exception as e:
                        logger.error(f"Erro DataChannel: {e}")

            player = MediaPlayer('rtsp://127.0.0.1:8554/robot', options={
                'rtsp_transport': 'tcp', 'fflags': 'nobuffer+discardcorrupt', 'flags': 'low_delay', 'stimeout': '5000000',
            })

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
            await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer_data['sdp'], type=offer_data['type']))
            self._remote_description_set = True

            await self._flush_pending_candidates()

            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            
            def _send_answer():
                try:
                    self.doc_ref.update({
                        'webrtc_session.answer': {'sdp': self.pc.localDescription.sdp, 'type': self.pc.localDescription.type}
                    })
                except Exception as e:
                    logger.error(f"Erro ao publicar Answer: {e}")
            
            self.loop.run_in_executor(None, _send_answer)
            logger.info("✓ Answer publicada. Aguardando ICE...")

        except Exception as e:
            logger.error(f"Erro fatal no Handshake WebRTC: {e}", exc_info=True)
        finally:
            self.handling_offer = False

    def process_candidates(self, app_candidates):
        if not self.pc: return
        for cand_data in app_candidates:
            cand_str = cand_data.get('candidate')
            if cand_str and cand_str not in self._processed_app_candidates:
                self._processed_app_candidates.add(cand_str)
                if self._remote_description_set:
                    asyncio.run_coroutine_threadsafe(self._add_ice_candidate(cand_data), self.loop)
                else:
                    self._pending_candidates.append(cand_data)

    async def _add_ice_candidate(self, c):
        if not self.pc or not self._remote_description_set: return
        try:
            from aiortc.sdp import candidate_from_sdp
            parsed = candidate_from_sdp(str(c.get('candidate', '')).replace("candidate:", ""))
            parsed.sdpMid = str(c.get('sdpMid', '0'))
            parsed.sdpMLineIndex = int(c.get('sdpMLineIndex', 0))
            await self.pc.addIceCandidate(parsed)
        except Exception as e:
            logger.warning(f"Candidato ICE ignorado: {e}")

    async def _flush_pending_candidates(self):
        if not self._pending_candidates: return
        for cand in self._pending_candidates:
            await self._add_ice_candidate(cand)
        self._pending_candidates.clear()

    async def close(self):
        """Fecha conexões ativas e limpa variáveis de sessão."""
        if self.pc:
            pc_ref = self.pc
            self.pc = None
            try:
                pc_ref.on("iceconnectionstatechange", None)
                pc_ref.on("icecandidate", None)
                pc_ref.on("datachannel", None)
                await pc_ref.close()
            except Exception:
                pass

        self._processed_app_candidates.clear()
        self._pending_candidates.clear()
        self._remote_description_set = False
        self.handling_offer = False