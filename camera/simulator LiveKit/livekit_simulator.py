import asyncio
import cv2
import logging
import os
import time
import socket
from dotenv import load_dotenv
from livekit import rtc

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("agromotion-sim-livekit")

load_dotenv()

class LiveKitRobotSim:
    def __init__(self):
        self.url = os.getenv("LIVEKIT_URL")
        self.api_key = os.getenv("LIVEKIT_API_KEY")
        self.api_secret = os.getenv("LIVEKIT_API_SECRET")
        self.video_path = os.getenv("VIDEO_PATH")
        self.robot_id = os.getenv("ROBOT_ID", "robot_01")
        
        self.cap = None
        self.room = rtc.Room()

    def check_essentials(self):
        logger.info("🔍 Verificando ficheiros e rede...")
        net = True
        try: socket.create_connection(("8.8.8.8", 53), timeout=3)
        except: net = False
        
        video_exists = os.path.exists(self.video_path or "")
        keys_exist = all([self.url, self.api_key, self.api_secret])
        
        print(f"[{'OK' if net else '!!'}] Internet")
        print(f"[{'OK' if video_exists else '!!'}] Ficheiro de Vídeo")
        print(f"[{'OK' if keys_exist else '!!'}] Chaves LiveKit")
        
        return net and video_exists and keys_exist

    async def publish_video(self):
        """Captura frames do vídeo e envia para o LiveKit"""
        # Configuração da fonte de vídeo (480p para otimização multi-user)
        source = rtc.VideoSource(854, 480)
        track = rtc.LocalVideoTrack.create_video_track("camera", source)
        
        options = rtc.TrackPublishOptions(
            display_name="Robot Camera",
            video_codec=rtc.VideoCodec.VP8,
            source=rtc.TrackSource.SOURCE_CAMERA
        )

        publication = await self.room.local_participant.publish_track(track, options)
        logger.info(f"🚀 Stream publicada com sucesso: {publication.sid}")

        self.cap = cv2.VideoCapture(self.video_path)
        
        while self.room.isconnected():
            ret, frame = self.cap.read()
            if not ret:
                # Loop do vídeo
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue

            # 1. Resize para 480p
            frame = cv2.resize(frame, (854, 480))
            
            # 2. LiveKit exige RGBA
            rgba_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
            
            # 3. Criar e enviar o VideoFrame
            video_frame = rtc.VideoFrame(
                854, 480, 
                rtc.VideoBufferType.RGBA, 
                rgba_frame.tobytes()
            )
            source.capture_frame(video_frame)
            
            # Controlar o framerate (aprox 24-30 FPS)
            await asyncio.sleep(0.033)

    async def run(self):
        if not self.check_essentials():
            logger.error("❌ Falha no check-up. Verifica o teu ficheiro .env")
            return

        # Gerar Token de Acesso
        token = rtc.AccessToken(self.api_key, self.api_secret) \
            .with_identity(self.robot_id) \
            .with_name("Agromotion Simulator") \
            .with_grants(rtc.VideoGrants(room_join=True)) \
            .to_jwt()

        try:
            logger.info(f"🔗 Conectando ao LiveKit em {self.url}...")
            await self.room.connect(self.url, token)
            logger.info(f"✅ Conectado à sala! ID da Sessão: {self.room.sid}")

            # Iniciar a publicação de vídeo
            await self.publish_video()

        except Exception as e:
            logger.error(f"❌ Erro na simulação: {e}")
        finally:
            if self.cap: self.cap.release()
            await self.room.disconnect()

if __name__ == "__main__":
    sim = LiveKitRobotSim()
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        logger.info("🛑 Simulação encerrada.")