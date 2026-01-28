import asyncio
import json
import cv2
import logging
import os
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiohttp_cors import setup, ResourceOptions
from av import VideoFrame

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agromotion-sim")

class LocalVideoTrack(VideoStreamTrack):
    def __init__(self, path):
        super().__init__()
        self.cap = cv2.VideoCapture(path)
        self.target_width = None 
        self.target_height = None
        
    def set_quality(self, height):
        if height in ["original", "auto"]:
            self.target_width = None
            self.target_height = None
            logger.info("[QUALITY] Reset para resolução original")
        else:
            try:
                self.target_height = int(height)
                self.target_width = int((self.target_height * 16) / 9)
                logger.info(f"[QUALITY] Redimensionando para: {self.target_width}x{self.target_height}")
            except:
                pass

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        if not ret:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.cap.read()
        
        if self.target_height and self.target_width:
            frame = cv2.resize(frame, (self.target_width, self.target_height), interpolation=cv2.INTER_AREA)

        new_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        new_frame.pts = pts
        new_frame.time_base = time_base
        return new_frame

async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    pc = RTCPeerConnection()
    
    video_path = r"C:\Users\user\Videos\video_teste2.MP4"
    video_track = LocalVideoTrack(video_path)

    @pc.on("datachannel")
    def on_datachannel(channel):
        @channel.on("message")
        def on_message(message):
            if isinstance(message, str):
                data = json.loads(message)
                if data.get("type") == "SET_QUALITY":
                    video_track.set_quality(data.get("value"))

    # 1. Aplicamos a oferta primeiro
    await pc.setRemoteDescription(offer)

    # 2. Em vez de pc.addTrack, vamos associar a track ao transceiver existente
    for transceiver in pc.getTransceivers():
        if transceiver.kind == "video":
            pc.addTrack(video_track) 
            # O aiortc associa automaticamente ao transceiver correto aqui

    # 3. Criamos a resposta
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}),
    )

app = web.Application()
cors = setup(app, defaults={"*": ResourceOptions(allow_credentials=True, expose_headers="*", allow_headers="*")})
resource = app.router.add_post("/offer", offer)
cors.add(resource)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)