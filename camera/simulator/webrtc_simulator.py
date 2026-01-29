import asyncio
import json
import cv2
import logging
import os
import time
import socket
import numpy as np
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCRtpSender
from av import VideoFrame

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("agromotion-sim")

load_dotenv()

class LocalVideoTrack(VideoStreamTrack):
    def __init__(self, path):
        super().__init__()
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            logger.error(f"❌ Não foi possível abrir o vídeo: {path}")
        
        self.target_width = 1280
        self.target_height = 720
        self.frame_count = 0
        self.last_fps_check = time.time()
        self.current_fps = 0

    def set_quality(self, height):
        if height in ["original", "auto"]:
            self.target_width, self.target_height = 1280, 720
        else:
            self.target_height = int(height)
            self.target_width = int((self.target_height * 16) / 9)
        logger.info(f"[QUALIDADE] Simulação ajustada: {self.target_width}x{self.target_height}")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        
        if not ret:
            # Loop do vídeo simulado
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.cap.read()

        frame = cv2.resize(frame, (self.target_width, self.target_height), interpolation=cv2.INTER_LINEAR)

        # Cálculo de FPS real da simulação
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
        self.robot_id = os.getenv("ROBOT_ID")
        self.cert_path = os.getenv("FIREBASE_CERT_PATH")
        self.video_path = os.getenv("VIDEO_PATH")
        self.pc = None
        self.data_channel = None
        self.db = None
        self.doc_ref = None

    def check_essentials(self):
        """Validação rápida para o simulador"""
        logger.info("🔍 Verificando ficheiros e rede...")
        net = True
        try: socket.create_connection(("8.8.8.8", 53), timeout=3)
        except: net = False
        
        video_exists = os.path.exists(self.video_path or "")
        cert_exists = os.path.exists(self.cert_path or "")
        
        print(f"[{'OK' if net else '!!'}] Internet")
        print(f"[{'OK' if video_exists else '!!'}] Ficheiro de Vídeo")
        print(f"[{'OK' if cert_exists else '!!'}] Certificado Firebase")
        
        return net and video_exists and cert_exists

    async def send_telemetry(self, track):
        """Loop de telemetria simulada (Seguro contra quedas)"""
        try:
            while self.data_channel and self.data_channel.readyState == "open":
                stats = {
                    "type": "TELEMETRY",
                    "fps": track.current_fps,
                    "res": f"{track.target_width}x{track.target_height}",
                    "cpu": 12.5, # Simulado
                    "temp": 38.4  # Simulado
                }
                try:
                    self.data_channel.send(json.dumps(stats))
                except: break
                await asyncio.sleep(1)
        except asyncio.CancelledError: pass

    async def process_offer(self, offer_dict):
        try:
            logger.info("🔥 Oferta recebida! Estabelecendo WebRTC simulado...")
            if self.pc: await self.pc.close()
            
            self.pc = RTCPeerConnection()
            
            # --- CORREÇÃO DE CODECS: Forçar VP8 ---
            # No aiortc, obtemos as capacidades através do RTCRtpSender
            capabilities = RTCRtpSender.getCapabilities("video")
            # Filtramos apenas o codec VP8
            preferences = [c for c in capabilities.codecs if c.name == "VP8"]
            
            track = LocalVideoTrack(self.video_path)

            @self.pc.on("datachannel")
            def on_datachannel(channel):
                self.data_channel = channel
                @channel.on("message")
                def on_message(message):
                    data = json.loads(message)
                    if data.get("type") == "PING":
                        channel.send(json.dumps({"type": "PONG", "timestamp": data.get("timestamp")}))
                
                asyncio.create_task(self.send_telemetry(track))

            offer = RTCSessionDescription(sdp=offer_dict["sdp"], type=offer_dict["type"])
            await self.pc.setRemoteDescription(offer)
            
            # Adicionar a track e definir a preferência de codec no Transceiver
            for transceiver in self.pc.getTransceivers():
                if transceiver.kind == "video":
                    # Definimos as preferências de codec ANTES de criar a Answer
                    transceiver.setCodecPreferences(preferences)
            
            # Adicionamos a track ao PeerConnection
            self.pc.addTrack(track)

            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            
            self.doc_ref.update({
                "answer": {
                    "sdp": self.pc.localDescription.sdp, 
                    "type": self.pc.localDescription.type
                }
            })
            logger.info("🚀 Simulador Conectado via VP8 (Compatibilidade Android)!")
            
        except Exception as e:
            logger.error(f"❌ Erro no processamento: {e}")

    async def run(self):
        if not self.check_essentials():
            logger.error("❌ Falha no check-up. Verifica o teu ficheiro .env")
            return

        # Inicializa Firebase
        cred = credentials.Certificate(self.cert_path)
        firebase_admin.initialize_app(cred)
        self.db = firestore.client()
        self.doc_ref = self.db.collection("robot").document(self.robot_id)
        self.doc_ref.update({"answer": None, "offer": None})

        logger.info(f"🛰️  Simulador '{self.robot_id}' em escuta...")
        
        loop = asyncio.get_running_loop()
        def on_snap(docs, changes, read_time):
            for doc in docs:
                data = doc.to_dict()
                if data and data.get("offer") and not data.get("answer"):
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(self.process_offer(data["offer"])))
        
        self.doc_ref.on_snapshot(on_snap)
        
        while True:
            await asyncio.sleep(1)

if __name__ == "__main__":
    sim = RobotSim()
    try:
        # Padrão mais estável para Windows
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(sim.run())
    except KeyboardInterrupt:
        logger.info("🛑 Simulação encerrada.")