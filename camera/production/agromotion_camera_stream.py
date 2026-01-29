import asyncio
import json
import cv2
import logging
import os
import time
import socket
import numpy as np
import psutil
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("agromotion-pi")

load_dotenv()

class CameraStreamTrack(VideoStreamTrack):
    def __init__(self, cap):
        super().__init__()
        self.cap = cap
        self.target_width = 1280
        self.target_height = 720
        self.frame_count = 0
        self.last_fps_check = time.time()
        self.current_fps = 0
        
        # Configuração inicial de hardware
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

    def set_quality(self, height):
        if height in ["original", "auto"]:
            self.target_width, self.target_height = 1280, 720
        else:
            self.target_height = int(height)
            self.target_width = int((self.target_height * 16) / 9)
        logger.info(f"[QUALIDADE] Ajustada para: {self.target_width}x{self.target_height}")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        
        if not ret:
            # Frame de erro se a câmara falhar em runtime
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            cv2.putText(frame, "ERRO DE CAPTURA", (450, 360), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        else:
            frame = cv2.resize(frame, (self.target_width, self.target_height), interpolation=cv2.INTER_LINEAR)

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

class AgromotionRobot:
    def __init__(self):
        self.robot_id = os.getenv("ROBOT_ID")
        self.cert_path = os.getenv("FIREBASE_CERT_PATH")
        self.cap = None
        self.pc = None
        self.data_channel = None
        self.db = None
        self.doc_ref = None

    # --- Diagnósticos ---
    def check_internet(self):
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except: return False

    def check_camera(self):
        backend = cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_V4L2
        cap = cv2.VideoCapture(0, backend)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                self.cap = cap # Mantemos a câmara aberta para a track
                return True
        if cap: cap.release()
        return False

    async def send_telemetry(self, track):
        try:
            while self.data_channel and self.data_channel.readyState == "open":
                temp = 0.0
                try:
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        temp = float(f.read()) / 1000.0
                except: pass

                stats = {
                    "type": "TELEMETRY",
                    "fps": track.current_fps,
                    "res": f"{track.target_width}x{track.target_height}",
                    "cpu": psutil.cpu_percent(),
                    "temp": round(temp, 1)
                }
                self.data_channel.send(json.dumps(stats))
                await asyncio.sleep(1)
        except: pass

    async def process_offer(self, offer_dict):
        try:
            logger.info("✅ Oferta recebida! Estabelecendo WebRTC...")
            if self.pc: await self.pc.close()
            
            self.pc = RTCPeerConnection()
            track = CameraStreamTrack(self.cap)

            @self.pc.on("datachannel")
            def on_datachannel(channel):
                self.data_channel = channel
                @channel.on("message")
                def on_message(message):
                    data = json.loads(message)
                    if data.get("type") == "SET_QUALITY":
                        track.set_quality(data.get("value"))
                    elif data.get("type") == "PING":
                        channel.send(json.dumps({"type": "PONG", "timestamp": data.get("timestamp")}))
                
                asyncio.create_task(self.send_telemetry(track))

            offer = RTCSessionDescription(sdp=offer_dict["sdp"], type=offer_dict["type"])
            await self.pc.setRemoteDescription(offer)
            
            for transceiver in self.pc.getTransceivers():
                if transceiver.kind == "video":
                    self.pc.addTrack(track)

            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            self.doc_ref.update({"answer": {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}})
            logger.info("🚀 Conexão estabelecida com sucesso!")
        except Exception as e:
            logger.error(f"Erro no WebRTC: {e}")

    async def run(self):
        while True:
            print("\n" + "="*40 + "\n🔍 DIAGNÓSTICO DE ARRANQUE\n" + "="*40)
            
            net = self.check_internet()
            print(f"[{'✅' if net else '❌'}] Internet")
            
            cam = self.check_camera()
            print(f"[{'✅' if cam else '❌'}] Câmara")
            
            env = all([self.robot_id, self.cert_path]) and os.path.exists(self.cert_path or "")
            print(f"[{'✅' if env else '❌'}] Configuração (.env)\n" + "="*40)

            if net and cam and env:
                logger.info("✅ Tudo pronto. Entrando em modo de escuta...")
                try:
                    if not firebase_admin._apps:
                        cred = credentials.Certificate(self.cert_path)
                        firebase_admin.initialize_app(cred)
                    
                    self.db = firestore.client()
                    self.doc_ref = self.db.collection("robot").document(self.robot_id)
                    self.doc_ref.update({"answer": None, "offer": None})

                    loop = asyncio.get_running_loop()
                    def on_snap(docs, changes, read_time):
                        for doc in docs:
                            data = doc.to_dict()
                            if data and data.get("offer") and not data.get("answer"):
                                loop.call_soon_threadsafe(lambda: asyncio.create_task(self.process_offer(data["offer"])))
                    
                    self.doc_ref.on_snapshot(on_snap)
                    
                    while self.check_internet(): # Mantém vivo enquanto houver net
                        await asyncio.sleep(5)
                    
                    logger.warning("🌐 Internet perdida. Reiniciando ciclo de diagnóstico...")
                except Exception as e:
                    logger.error(f"Falha na lógica principal: {e}")
            else:
                logger.error("❌ Falha no Check-up. Tentando novamente em 30s...")
            
            if self.cap: self.cap.release()
            await asyncio.sleep(30)

if __name__ == "__main__":
    robot = AgromotionRobot()
    try:
        asyncio.run(robot.run())
    except KeyboardInterrupt:
        if robot.cap: robot.cap.release()
        logger.info("🛑 Sistema encerrado.")