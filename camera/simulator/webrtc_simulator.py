import asyncio
import json
import cv2
import logging
import os
import time
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agromotion-sim")

load_dotenv()

class LocalVideoTrack(VideoStreamTrack):
    def __init__(self, path):
        super().__init__()
        self.cap = cv2.VideoCapture(path)
        self.target_width = None 
        self.target_height = None
        self.frame_count = 0
        self.last_fps_check = time.time()
        self.current_fps = 0

    def set_quality(self, height):
        if height in ["original", "auto"]:
            self.target_width = None
            self.target_height = None
            logger.info("[QUALITY] Reset para original")
        else:
            self.target_height = int(height)
            self.target_width = int((self.target_height * 16) / 9)
            logger.info(f"[QUALITY] Resize: {self.target_width}x{self.target_height}")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        if not ret:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.cap.read()
        
        if self.target_height and self.target_width:
            frame = cv2.resize(frame, (self.target_width, self.target_height), interpolation=cv2.INTER_AREA)

        # Cálculo de FPS real de processamento
        self.frame_count += 1
        now = time.time()
        if now - self.last_fps_check >= 1.0:
            self.current_fps = self.frame_count
            self.frame_count = 0
            self.last_fps_check = now

        new_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        new_frame.pts = pts
        new_frame.time_base = time_base
        return new_frame

class RobotSim:
    def __init__(self):
        cred_path = os.getenv("FIREBASE_CERT_PATH")
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        self.db = firestore.client()
        self.robot_id = os.getenv("ROBOT_ID")
        self.video_path = os.getenv("VIDEO_PATH")
        self.pc = None
        self.doc_ref = self.db.collection("robot").document(self.robot_id)
        self.doc_ref.update({"answer": None, "offer": None})
        self.loop = None
        self.data_channel = None

    async def send_telemetry(self, video_track):
        """Envia dados técnicos via DataChannel de forma segura"""
        try:
            while self.data_channel and self.data_channel.readyState == "open":
                stats = {
                    "type": "TELEMETRY",
                    "fps": video_track.current_fps,
                    "res": f"{video_track.target_width or '1920'}x{video_track.target_height or '1080'}",
                    "cpu": 15.5,
                    "temp": 42.0
                }
                try:
                    self.data_channel.send(json.dumps(stats))
                except Exception:
                    break # Sai do loop se não conseguir enviar
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        logger.info("Loop de telemetria encerrado.")

    async def run(self):
        self.loop = asyncio.get_running_loop()
        logger.info(f"Simulador iniciado para {self.robot_id}...")
        
        def on_snapshot(doc_snapshot, changes, read_time):
            for doc in doc_snapshot:
                data = doc.to_dict()
                if data and data.get("offer") and not data.get("answer"):
                    self.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self.process_offer(data["offer"]))
                    )

        self.doc_ref.on_snapshot(on_snapshot)
        while True: await asyncio.sleep(3600)

    async def process_offer(self, offer_dict):
        try:
            if self.pc: await self.pc.close()
            self.pc = RTCPeerConnection()
            video_track = LocalVideoTrack(self.video_path)

            @self.pc.on("datachannel")
            def on_datachannel(channel):
                self.data_channel = channel
                @channel.on("message")
                def on_message(message):
                    data = json.loads(message)
                    # Lógica de Qualidade
                    if data.get("type") == "SET_QUALITY":
                        video_track.set_quality(data.get("value"))
                    # Lógica de Ping (Latência)
                    elif data.get("type") == "PING":
                        channel.send(json.dumps({"type": "PONG", "timestamp": data.get("timestamp")}))

                # Inicia loop de telemetria quando o canal abre
                asyncio.create_task(self.send_telemetry(video_track))

            offer = RTCSessionDescription(sdp=offer_dict["sdp"], type=offer_dict["type"])
            await self.pc.setRemoteDescription(offer)
            for transceiver in self.pc.getTransceivers():
                if transceiver.kind == "video": self.pc.addTrack(video_track)

            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            self.doc_ref.update({"answer": {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}})
            logger.info("Conexão estabelecida e Telemetria ativa!")
            
        except Exception as e:
            logger.error(f"Erro: {e}")

if __name__ == "__main__":
    sim = RobotSim()
    asyncio.run(sim.run())