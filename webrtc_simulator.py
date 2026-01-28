import asyncio
import json
import cv2
import logging
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiohttp_cors import setup, ResourceOptions
from av import VideoFrame

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pc")

class LocalVideoTrack(VideoStreamTrack):
    """Lê um vídeo local e transforma em frames WebRTC"""
    def __init__(self, path):
        super().__init__()
        print(f"[DEBUG] A abrir ficheiro de vídeo: {path}")
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            print(f"[ERRO] Não foi possível abrir o vídeo em: {path}")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        
        if not ret:
            print("[DEBUG] Fim do vídeo atingido. A reiniciar loop...")
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.cap.read()
        
        # Converter frame do OpenCV para PyAV
        new_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        new_frame.pts = pts
        new_frame.time_base = time_base
        return new_frame

async def offer(request):
    print(f"\n[DEBUG] Recebido pedido de conexão de: {request.remote}")
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    print("[DEBUG] PeerConnection criada.")

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print(f"[DEBUG] Estado da Conexão: {pc.connectionState}")
        if pc.connectionState == "failed" or pc.connectionState == "closed":
            await pc.close()

    video_path = r"PATH DO VÍDEO A MOSTRAR"  # Substitua pelo caminho do seu vídeo local
    video_track = LocalVideoTrack(video_path)
    pc.addTrack(video_track)
    print("[DEBUG] Track de vídeo local adicionada.")

    await pc.setRemoteDescription(offer)
    print("[DEBUG] Remote Description configurada.")
    
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    print("[DEBUG] Answer criada e Local Description definida.")

    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp, 
            "type": pc.localDescription.type
        }),
    )

app = web.Application()

cors = setup(app, defaults={
    "*": ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
    )
})

resource = app.router.add_post("/offer", offer)
cors.add(resource)

if __name__ == "__main__":
    print("--- Servidor de Simulação Agromotion Ativo ---")
    print("Porta: 8080 | Endpoint: /offer")
    web.run_app(app, host="0.0.0.0", port=8080)