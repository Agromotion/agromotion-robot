"""
Integração de streaming de vídeo com Mediamtx
Gere a captura da libcamera + FFmpeg e faz o stream via RTSP
"""

import subprocess
import logging
import asyncio
import signal
import os
from typing import Optional, Dict, Any
from datetime import datetime

import config

logger = logging.getLogger(__name__)

class VideoStreamingManager:
    """
    Gere a captura de vídeo da Raspberry Pi Camera usando rpicam-vid,
    estabiliza via FFmpeg e envia para o Mediamtx para WebRTC fan-out.
    """

    def __init__(self):
        self.mediamtx_process: Optional[subprocess.Popen] = None
        self.video_process: Optional[subprocess.Popen] = None
        self.is_streaming = False
        self.stream_start_time = None

    async def start(self) -> bool:
        """Inicia o servidor Mediamtx e o pipeline de captura da câmara"""
        try:
            # 1. Iniciar Mediamtx
            if not await self._start_mediamtx():
                return False

            # 2. Esperar que o Mediamtx esteja pronto para receber o stream
            # Essencial para evitar o erro "failed to open output"
            await asyncio.sleep(5)

            # 3. Iniciar o pipeline de vídeo estabilizado
            if not await self._start_video_pipeline():
                await self.stop()
                return False

            self.is_streaming = True
            self.stream_start_time = datetime.now()
            logger.info("✓ Streaming de vídeo iniciado (rpicam-vid + FFmpeg)")
            return True

        except Exception as e:
            logger.error(f"Falha ao iniciar streaming de vídeo: {e}")
            return False

    async def _start_mediamtx(self) -> bool:
        """Inicia o servidor de media Mediamtx"""
        try:
            # Limpar instâncias anteriores
            subprocess.run(["pkill", "-9", "mediamtx"], capture_output=True)
            await asyncio.sleep(0.5)

            config_path = "/home/pi/raspberry/mediamtx.yml"
            command = ["mediamtx", config_path] if os.path.exists(config_path) else ["mediamtx"]
            
            self.mediamtx_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=self._ignore_sigint,
                text=True,
                bufsize=1
            )

            await asyncio.sleep(1)
            if self.mediamtx_process.poll() is not None:
                logger.error("Mediamtx falhou ao iniciar.")
                return False

            logger.info(f"Mediamtx pronto (PID: {self.mediamtx_process.pid})")
            return True

        except Exception as e:
            logger.error(f"Erro ao iniciar Mediamtx: {e}")
            return False

    async def _start_video_pipeline(self) -> bool:
        """Inicia o rpicam-vid em pipe para o FFmpeg"""
        try:
            # Construção do pipeline usando as variáveis do config.py
            # Usamos o FFmpeg como ponte para garantir que o RTSP não falha
            pipeline_cmd = (
                f"rpicam-vid -t 0 --inline --nopreview "
                f"--width {config.PI_CAMERA_WIDTH} "
                f"--height {config.PI_CAMERA_HEIGHT} "
                f"--framerate {config.PI_CAMERA_FPS} "
                f"--codec h264 -o - | "
                f"ffmpeg -i - -vcodec copy -f rtsp -rtsp_transport tcp "
                f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}"
            )

            self.video_process = subprocess.Popen(
                pipeline_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=self._ignore_sigint
            )

            # Verificar se o pipeline sobreviveu ao arranque
            await asyncio.sleep(2)
            if self.video_process.poll() is not None:
                return False

            logger.info("Pipeline de vídeo ativo.")
            return True

        except Exception as e:
            logger.error(f"Erro ao lançar pipeline: {e}")
            return False

    async def stop(self):
        """Pára todos os processos de forma limpa e agressiva para libertar a câmara"""
        try:
            # Matar processos específicos do pipeline
            subprocess.run(["pkill", "-9", "-f", "rpicam-vid"], capture_output=True)
            subprocess.run(["pkill", "-9", "-f", "ffmpeg"], capture_output=True)
            
            if self.mediamtx_process:
                self.mediamtx_process.terminate()
                self.mediamtx_process.wait(timeout=2)
                logger.info("Serviços de vídeo encerrados.")

            self.is_streaming = False
            self.stream_start_time = None
        except Exception as e:
            logger.error(f"Erro ao parar streaming: {e}")

    def get_stream_info(self) -> Dict[str, Any]:
        """Retorna informações do estado do stream para a telemetria do Firebase"""
        uptime = 0
        if self.stream_start_time:
            uptime = (datetime.now() - self.stream_start_time).total_seconds()
            
        return {
            "is_streaming": self.is_streaming,
            "uptime": round(uptime, 1),
            "resolution": f"{config.PI_CAMERA_WIDTH}x{config.PI_CAMERA_HEIGHT}",
            "fps": config.PI_CAMERA_FPS,
            "url": f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}"
        }

    def health_check(self) -> bool:
        """Verifica se os processos críticos estão vivos"""
        # Para o pipeline com shell=True, verificamos se o processo pai ainda existe
        mediamtx_alive = self.mediamtx_process and self.mediamtx_process.poll() is None
        video_alive = self.video_process and self.video_process.poll() is None
        
        return self.is_streaming and mediamtx_alive and video_alive

    @staticmethod
    def _ignore_sigint():
        """Evita que o sinal de interrupção mate os subprocessos prematuramente"""
        signal.signal(signal.SIGINT, signal.SIG_IGN)