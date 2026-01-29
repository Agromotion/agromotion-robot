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

# Importação da Picamera2 (Nativa para a imx708)
try:
    from pypicamera2 import Picamera2
except ImportError:
    # Fallback para ambientes de desenvolvimento sem câmara
    Picamera2 = None

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("agromotion-pi")

load_dotenv()

class CameraStreamTrack(VideoStreamTrack):
    def __init__(self, picam):
        super().__init__()
        self.picam = picam
        self.target_width = 1280
        self.target_height = 720
        self.frame_count = 0
        self.last_fps_check = time.time()
        self.current_fps = 0

    def set_quality(self, height):
        if height in ["original", "auto"]:
            h, w = 720, 1280
        else:
            h = int(height)
            w = int((h * 16) / 9)
        
        self.target_width, self.target_height = w, h
        logger.info(f"[QUALIDADE] Pedido de ajuste para: {w}x{h} (Via Software)")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        
        # Captura frame da Picamera2
        frame = self.picam.capture_array()
        
        # Converte de RGB (padrão Picamera2) para BGR (padrão OpenCV/Script)
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # Redimensionamento se necessário
        if frame.shape[0] != self.target_height:
            frame = cv2.resize(frame, (self.target_width, self.target_height), interpolation=cv2.INTER_LINEAR)

        # Cálculo de FPS
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
        self.picam = None
        self.pc = None
        self.data_channel = None
        self.db = None
        self.doc_ref = None

    def check_internet(self):
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except: return False

    def check_camera(self):
        try:
            if self.picam:
                return True
            
            # Inicializa Picamera2
            self.picam = Picamera2()
            # Configura a resolução de captura diretamente no sensor (ganha performance)
            self.picam.configure(self.picam.create_preview_configuration(main={"format": 'RGB888', "size": (1280, 720)}))
            self.picam.start()
            logger.info("📸 Picamera2 (imx708) iniciada com sucesso.")
            return True
        except Exception as e:
            logger.error(f"Erro ao aceder à câmara: {e}")
            if self.picam: 
                self.picam.stop()
                self.picam = None
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
            track = CameraStreamTrack(self.picam)

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
            logger.info("🚀 WebRTC conectado!")
        except Exception as e:
            logger.error(f"Erro no WebRTC: {e}")

    async def run(self):
        while True:
            print("\n" + "="*40 + "\n🔍 DIAGNÓSTICO AGROMOTION\n" + "="*40)
            
            net = self.check_internet()
            print(f"[{'✅' if net else '❌'}] Internet")
            
            cam = self.check_camera()
            print(f"[{'✅' if cam else '❌'}] Câmara (imx708)")
            
            env_exists = all([self.robot_id, self.cert_path]) and os.path.exists(self.cert_path or "")
            print(f"[{'✅' if env_exists else '❌'}] Configuração (.env)\n" + "="*40)

            if net and cam and env_exists:
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
                    
                    while self.check_internet():
                        await asyncio.sleep(5)
                    
                    logger.warning("🌐 Internet perdida. Reiniciando...")
                except Exception as e:
                    logger.error(f"Falha na lógica: {e}")
            else:
                logger.error("❌ Falha no Check-up. Tentando novamente em 15s...")
            
            await asyncio.sleep(15)

if __name__ == "__main__":
    robot = AgromotionRobot()
    try:
        asyncio.run(robot.run())
    except KeyboardInterrupt:
        if robot.picam: 
            robot.picam.stop()
        logger.info("🛑 Sistema encerrado.")