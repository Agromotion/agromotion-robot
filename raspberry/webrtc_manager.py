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
        
        self.pcs = {}
        self.handling_offers = set()
        self.on_disconnect: Optional[Callable] = None
        
        self._processed_app_candidates = {}
        self._pending_candidates = {}
        self._remote_description_set = {}

    def get_pc(self, email):
        return self.pcs.get(email)

    def is_handling(self, email):
        return email in self.handling_offers

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

    async def handle_offer(self, email, offer_data):
        self.handling_offers.add(email)
        ice_servers = [
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            RTCIceServer(urls=["stun:openrelay.metered.ca:80"]),
            RTCIceServer(
                urls=["turn:openrelay.metered.ca:80", "turn:openrelay.metered.ca:443", "turn:openrelay.metered.ca:443?transport=tcp"],
                username="openrelayproject", credential="openrelayproject"
            )
        ]

        self._remote_description_set[email] = False
        self._pending_candidates[email] = []
        self._processed_app_candidates[email] = set()

        if email in self.pcs:
            logger.info(f"Fechando conexão WebRTC anterior para {email}...")
            await self.close(email)
            await asyncio.sleep(0.2)

        try:
            if not await self._wait_for_stream_ready():
                logger.error("Abortando handshake: stream RTSP não disponível.")
                return

            pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
            self.pcs[email] = pc

            @pc.on("iceconnectionstatechange")
            async def on_ice_connection_state():
                pc_ref = self.pcs.get(email)
                if pc_ref:
                    state = pc_ref.iceConnectionState
                    logger.info(f"Estado de ligação ICE: {state}")
                    if state in ["failed", "closed", "disconnected"]:
                        await self.close(email)
                        if self.on_disconnect and email == self.access_manager.current_controller: 
                            if asyncio.iscoroutinefunction(self.on_disconnect):
                                await self.on_disconnect()
                            else:
                                self.on_disconnect()

            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    def _send_candidate():
                        try:
                            self.doc_ref.collection("viewers").document(email).update({
                                'robot_candidates': firestore.ArrayUnion([{
                                    'candidate': candidate.candidate, 'sdpMid': candidate.sdpMid, 'sdpMLineIndex': candidate.sdpMLineIndex
                                }])
                            })
                        except Exception as e:
                            logger.error(f"Erro ao enviar candidato ICE: {e}")
                    self.loop.run_in_executor(None, _send_candidate)

            @pc.on("datachannel")
            def on_datachannel(channel):
                logger.info(f"DataChannel '{channel.label}' estabelecido para {email}!")
                @channel.on("message")
                def on_message(message):
                    # Apenas o condutor principal pode comandar
                    if email == self.access_manager.current_controller:
                        self.access_manager.update_activity(email)
                        try:
                            cmd = json.loads(message)
                            if self.robot:
                                if cmd.get('drum') is not None:
                                    asyncio.run_coroutine_threadsafe(self.robot.execute_drum(cmd['drum'], email), self.loop)
                                elif cmd.get('x') is not None and cmd.get('y') is not None:
                                    asyncio.run_coroutine_threadsafe(self.robot.execute_command(cmd['x'], cmd['y'], email), self.loop)
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

            pc.addTrack(video_track)
            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_data['sdp'], type=offer_data['type']))
            self._remote_description_set[email] = True

            await self._flush_pending_candidates(email)

            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            
            def _send_answer():
                try:
                    self.doc_ref.collection("viewers").document(email).update({
                        'webrtc_session.answer': {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}
                    })
                except Exception as e:
                    logger.error(f"Erro ao publicar Answer: {e}")
            
            self.loop.run_in_executor(None, _send_answer)
            logger.info(f"✓ Answer publicada para {email}. Aguardando ICE...")

        except Exception as e:
            logger.error(f"Erro fatal no Handshake WebRTC: {e}", exc_info=True)
        finally:
            self.handling_offers.discard(email)

    def process_candidates(self, email, app_candidates):
        pc = self.pcs.get(email)
        if not pc: return
        for cand_data in app_candidates:
            cand_str = cand_data.get('candidate')
            if cand_str and cand_str not in self._processed_app_candidates.get(email, set()):
                self._processed_app_candidates.setdefault(email, set()).add(cand_str)
                if self._remote_description_set.get(email):
                    asyncio.run_coroutine_threadsafe(self._add_ice_candidate(email, cand_data), self.loop)
                else:
                    self._pending_candidates.setdefault(email, []).append(cand_data)

    async def _add_ice_candidate(self, email, c):
        pc = self.pcs.get(email)
        if not pc or not self._remote_description_set.get(email): return
        try:
            from aiortc.sdp import candidate_from_sdp
            parsed = candidate_from_sdp(str(c.get('candidate', '')).replace("candidate:", ""))
            parsed.sdpMid = str(c.get('sdpMid', '0'))
            parsed.sdpMLineIndex = int(c.get('sdpMLineIndex', 0))
            await pc.addIceCandidate(parsed)
        except Exception as e:
            logger.warning(f"Candidato ICE ignorado: {e}")

    async def _flush_pending_candidates(self, email):
        pending = self._pending_candidates.get(email, [])
        if not pending: return
        for cand in pending:
            await self._add_ice_candidate(email, cand)
        self._pending_candidates[email] = []

    async def close(self, email=None):
        """Fecha conexão de um espetador específico, ou todas se email for None."""
        if email is None:
            emails = list(self.pcs.keys())
            for e in emails:
                await self.close(e)
            return

        pc = self.pcs.pop(email, None)
        if pc:
            try:
                pc.on("iceconnectionstatechange", None)
                pc.on("icecandidate", None)
                pc.on("datachannel", None)
                await pc.close()
            except Exception:
                pass

        self._processed_app_candidates.pop(email, None)
        self._pending_candidates.pop(email, None)
        self._remote_description_set.pop(email, None)
        self.handling_offers.discard(email)