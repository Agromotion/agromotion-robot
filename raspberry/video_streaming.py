"""
Integração de streaming de vídeo com Mediamtx
Gere a captura da libcamera e faz o stream via RTSP para o Mediamtx
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
    Gere a captura de vídeo da Raspberry Pi Camera usando rpicam-vid
    e envia o stream via RTSP para o Mediamtx (WebRTC fan-out)
    """

    def __init__(self):
        self.mediamtx_process: Optional[subprocess.Popen] = None
        self.video_process: Optional[subprocess.Popen] = None
        self.is_streaming = False
        self.stream_start_time = None

    async def start(self) -> bool:
        """Inicia o servidor Mediamtx e o pipeline de captura da câmara"""
        try:
            # Iniciar Mediamtx
            if not await self._start_mediamtx():
                return False

            # Esperar que o Mediamtx esteja pronto para receber o stream
            await asyncio.sleep(6)

            # Iniciar captura da câmara real
            if not await self._start_libcamera():
                await self.stop()
                return False

            self.is_streaming = True
            self.stream_start_time = datetime.now()
            logger.info("Pipeline de vídeo iniciado com sucesso (rpicam-vid)")
            return True

        except Exception as e:
            logger.error(f"Falha ao iniciar streaming de vídeo: {e}")
            return False

    async def _start_mediamtx(self) -> bool:
        """Inicia o servidor de media Mediamtx"""
        try:
            # Matar instâncias antigas para evitar conflitos de porta (RTSP/WebRTC)
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
                logger.error("Mediamtx falhou ao iniciar (Processo terminou precocemente)")
                return False

            logger.info(f"Mediamtx iniciado (PID: {self.mediamtx_process.pid})")
            return True

        except Exception as e:
            logger.error(f"Falha ao iniciar Mediamtx: {e}")
            return False

    async def _start_libcamera(self) -> bool:
        """Inicia o rpicam-vid e envia para o endpoint RTSP do Mediamtx"""
        try:
            # Comando otimizado para o hardware do Raspberry Pi
            command = [
                "rpicam-vid",
                "-t", "0",  # Execução contínua
                "--codec", "h264",
                "--width", str(config.PI_CAMERA_WIDTH),
                "--height", str(config.PI_CAMERA_HEIGHT),
                "--framerate", str(config.PI_CAMERA_FPS),
                "--bitrate", str(config.PI_CAMERA_BITRATE),
                "--inline", # Insere headers SPS/PPS para reconexão rápida
                "--output", f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}?rtsp_transport=tcp",
                "--verbose", "0"
            ]

            self.video_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=self._ignore_sigint
            )

            await asyncio.sleep(1)
            if self.video_process.poll() is not None:
                _, stderr = self.video_process.communicate()
                logger.error(f"rpicam-vid falhou: {stderr.decode()}")
                return False

            logger.info(f"rpicam-vid iniciado (PID: {self.video_process.pid})")
            return True

        except Exception as e:
            logger.error(f"Falha ao iniciar libcamera: {e}")
            return False

    async def stop(self):
        """Pára todos os processos de vídeo de forma limpa"""
        try:
            if self.video_process:
                self.video_process.terminate()
                self.video_process.wait(timeout=2)
                logger.info("rpicam-vid parado")

            if self.mediamtx_process:
                self.mediamtx_process.terminate()
                self.mediamtx_process.wait(timeout=2)
                logger.info("Mediamtx parado")

            self.is_streaming = False
        except Exception as e:
            logger.error(f"Erro ao parar streaming: {e}")

    def get_stream_info(self) -> Dict[str, Any]:
        """Retorna informações do estado do stream para a telemetria"""
        uptime = (datetime.now() - self.stream_start_time).total_seconds() if self.stream_start_time else 0
        return {
            "is_streaming": self.is_streaming,
            "uptime": uptime,
            "resolution": f"{config.PI_CAMERA_WIDTH}x{config.PI_CAMERA_HEIGHT}",
            "fps": config.PI_CAMERA_FPS,
            "url": f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}"
        }

    def health_check(self) -> bool:
        """Verifica se os processos estão vivos"""
        return (
            self.is_streaming and
            self.mediamtx_process and self.mediamtx_process.poll() is None and
            self.video_process and self.video_process.poll() is None
        )

    @staticmethod
    def _ignore_sigint():
        """Impede que Ctrl+C mate os subprocessos antes do firmware principal"""
        signal.signal(signal.SIGINT, signal.SIG_IGN)