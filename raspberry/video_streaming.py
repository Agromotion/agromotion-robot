"""
Video streaming integration with Mediamtx
Handles libcamera capture or video file playback and streams via RTSP to Mediamtx
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
    Manages video capture from Raspberry Pi camera using libcamera
    and streams via RTSP to Mediamtx for WebRTC fan-out
    
    Supports two modes:
    - camera: Use Raspberry Pi camera via libcamera-vid (default)
    - video: Use video file with FFmpeg (for testing)
    """

    def __init__(self, mode: str = "camera"):
        self.mode = mode.lower()  # "camera" or "video"
        self.mediamtx_process: Optional[subprocess.Popen] = None
        self.video_process: Optional[subprocess.Popen] = None  # libcamera or ffmpeg
        self.is_streaming = False
        self.stream_start_time = None
        self.fps_counter = 0
        self.frame_drops = 0

    async def start(self) -> bool:
        """Start Mediamtx and video capture pipeline"""
        try:
            # Start Mediamtx
            if not await self._start_mediamtx():
                return False

            # Wait for Mediamtx to be ready
            await asyncio.sleep(2)

            # Start video source (camera or file)
            if self.mode == "video":
                if not await self._start_video_file():
                    await self.stop()
                    return False
            else:  # Default: camera
                if not await self._start_libcamera():
                    await self.stop()
                    return False

            self.is_streaming = True
            self.stream_start_time = datetime.now()
            source_name = "Camera (libcamera)" if self.mode == "camera" else "Video file (FFmpeg)"
            logger.info(f" Video streaming pipeline started ({source_name})")
            return True

        except Exception as e:
            logger.error(f" Failed to start video streaming: {e}")
            return False

    async def _start_mediamtx(self) -> bool:
        """Start Mediamtx media server"""
        try:
            # Check if mediamtx binary exists
            result = subprocess.run(
                ["which", "mediamtx"],
                capture_output=True,
                timeout=5
            )
            
            if result.returncode != 0:
                logger.error("mediamtx binary not found. Install it first!")
                return False

            # Kill any existing mediamtx processes to avoid port conflicts
            logger.info("Checking for existing mediamtx processes...")
            try:
                result = subprocess.run(["pkill", "-9", "mediamtx"], capture_output=True, timeout=2)
                if result.returncode == 0:
                    logger.info("Killed existing mediamtx process")
                await asyncio.sleep(1)
            except:
                pass

            # Get the absolute path to mediamtx.yml in /home/pi/agromotion-robot/raspberry/
            config_path = "/home/pi/agromotion-robot/raspberry/mediamtx.yml"
            
            logger.info(f"Looking for config at: {config_path}")
            
            # Start Mediamtx with config if exists, otherwise without
            if os.path.exists(config_path):
                command = ["mediamtx", config_path]
                logger.info(f"Starting mediamtx with config: {config_path}")
            else:
                command = ["mediamtx"]
                logger.info("Starting mediamtx with default configuration")

            # Start Mediamtx with output redirection for debugging
            self.mediamtx_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=self._ignore_sigint,
                text=True,
                bufsize=1
            )

            # Give it time to start
            await asyncio.sleep(2)
            
            if self.mediamtx_process.poll() is not None:
                # Process exited - get output
                try:
                    output, _ = self.mediamtx_process.communicate(timeout=1)
                    logger.error(f"Mediamtx failed to start")
                    logger.error(f"Output: {output}")
                except:
                    logger.error("Mediamtx failed to start (no output)")
                return False

            logger.info(f"  Mediamtx started (PID: {self.mediamtx_process.pid})")
            logger.info(f"  RTSP: rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}")
            logger.info(f"  WebRTC: http://127.0.0.1:{config.MEDIAMTX_WEBRTC_PORT}")
            return True

        except Exception as e:
            logger.error(f"  Failed to start Mediamtx: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def _start_libcamera(self) -> bool:
        """Start libcamera video capture and stream to Mediamtx RTSP"""
        try:
            # Build libcamera-vid command
            # Output to RTSP endpoint that Mediamtx is listening on
            command = [
                "libcamera-vid",
                "-t", "0",  # Run forever
                "--codec", "h264",
                "--width", str(config.PI_CAMERA_WIDTH),
                "--height", str(config.PI_CAMERA_HEIGHT),
                "--framerate", str(config.PI_CAMERA_FPS),
                "--bitrate", str(config.PI_CAMERA_BITRATE),
                "--output", f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}",
                "--verbose", "0"  # Minimal output
            ]

            self.video_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=self._ignore_sigint
            )

            # Verify it started
            await asyncio.sleep(1)
            if self.video_process.poll() is not None:
                _, stderr = self.video_process.communicate()
                logger.error(f"libcamera-vid failed: {stderr.decode()}")
                return False

            logger.info(f"  libcamera-vid started (PID: {self.video_process.pid})")
            logger.info(f"  Streaming to: rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}")
            return True

        except Exception as e:
            logger.error(f"  Failed to start libcamera: {e}")
            return False

    async def _start_video_file(self) -> bool:
        """Start FFmpeg video file streaming to Mediamtx RTSP"""
        try:
            video_file = config.VIDEO_FILE_PATH
            
            # Check if file exists
            if not os.path.exists(video_file):
                logger.error(f"Video file not found: {video_file}")
                logger.error(f"Please configure VIDEO_FILE_PATH in .env")
                return False
            
            # Build FFmpeg command to stream video file to RTSP
            # Optimized for low CPU usage: copy codecs when possible, fast preset
            command = [
                "ffmpeg",
                "-re",  # Read at native frame rate
                "-stream_loop", "-1",  # Loop indefinitely
                "-i", video_file,
                "-c:v", "copy",  # Copy video codec (no transcoding = low CPU)
                "-c:a", "copy",  # Copy audio codec (no transcoding = low CPU)
                "-f", "rtsp",    # Output format
                "-rtsp_transport", "tcp",
                f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}"
            ]
            
            self.video_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=self._ignore_sigint
            )
            
            # Verify it started
            await asyncio.sleep(1)
            if self.video_process.poll() is not None:
                _, stderr = self.video_process.communicate()
                logger.error(f"FFmpeg failed: {stderr.decode()}")
                return False
            
            logger.info(f"✓ FFmpeg streaming video file (PID: {self.video_process.pid})")
            logger.info(f"  Video: {video_file} (looping, copy codec for low CPU)")
            return True
        
        except Exception as e:
            logger.error(f"✗ Failed to start video file streaming: {e}")
            return False

    async def stop(self):
        """Stop video streaming"""
        try:
            # Stop video process (libcamera or FFmpeg)
            if self.video_process:
                self.video_process.terminate()
                try:
                    self.video_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.video_process.kill()
                source_name = "libcamera-vid" if self.mode == "camera" else "FFmpeg"
                logger.info(f"✓ {source_name} stopped")

            # Stop Mediamtx
            if self.mediamtx_process:
                self.mediamtx_process.terminate()
                try:
                    self.mediamtx_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.mediamtx_process.kill()
                logger.info("✓ Mediamtx stopped")

            self.is_streaming = False

        except Exception as e:
            logger.error(f"Error stopping video streaming: {e}")

    def get_stream_info(self) -> Dict[str, Any]:
        """Get streaming information"""
        uptime = None
        if self.stream_start_time:
            uptime = (datetime.now() - self.stream_start_time).total_seconds()

        return {
            "is_streaming": self.is_streaming,
            "video_mode": self.mode,
            "mediamtx_running": self.mediamtx_process and self.mediamtx_process.poll() is None,
            "video_process_running": self.video_process and self.video_process.poll() is None,
            "mediamtx_pid": self.mediamtx_process.pid if self.mediamtx_process else None,
            "video_process_pid": self.video_process.pid if self.video_process else None,
            "uptime_seconds": uptime,
            "mediamtx_rtsp_url": f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}",
            "mediamtx_webrtc_port": config.MEDIAMTX_WEBRTC_PORT,
            "camera_resolution": f"{config.PI_CAMERA_WIDTH}x{config.PI_CAMERA_HEIGHT}",
            "camera_fps": config.PI_CAMERA_FPS,
        }

    def health_check(self) -> bool:
        """Check if both processes are running"""
        return (
            self.is_streaming and
            self.mediamtx_process and self.mediamtx_process.poll() is None and
            self.video_process and self.video_process.poll() is None
        )

    @staticmethod
    def _ignore_sigint():
        """Ignore SIGINT in subprocesses to prevent early termination"""
        signal.signal(signal.SIGINT, signal.SIG_IGN)


# Alternative method using FFmpeg (if libcamera-vid not available)
class VideoStreamingManagerFFmpeg:
    """
    Alternative video streaming using FFmpeg instead of libcamera
    Less efficient but more compatible
    """

    def __init__(self):
        self.mediamtx_process: Optional[subprocess.Popen] = None
        self.ffmpeg_process: Optional[subprocess.Popen] = None
        self.is_streaming = False
        self.stream_start_time = None

    async def start(self) -> bool:
        """Start Mediamtx and FFmpeg pipeline"""
        try:
            if not await self._start_mediamtx():
                return False

            await asyncio.sleep(2)

            if not await self._start_ffmpeg():
                await self.stop()
                return False

            self.is_streaming = True
            self.stream_start_time = datetime.now()
            logger.info("✓ FFmpeg video streaming pipeline started")
            return True

        except Exception as e:
            logger.error(f"✗ Failed to start FFmpeg streaming: {e}")
            return False

    async def _start_mediamtx(self) -> bool:
        """Start Mediamtx (same as above)"""
        try:
            result = subprocess.run(
                ["which", "mediamtx"],
                capture_output=True,
                timeout=5
            )
            
            if result.returncode != 0:
                logger.error("mediamtx binary not found")
                return False

            self.mediamtx_process = subprocess.Popen(
                ["mediamtx", config.MEDIAMTX_CONFIG_PATH],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=signal.signal(signal.SIGINT, signal.SIG_IGN)
            )

            await asyncio.sleep(1)
            if self.mediamtx_process.poll() is not None:
                return False

            logger.info(f"✓ Mediamtx started")
            return True

        except Exception as e:
            logger.error(f"Failed to start Mediamtx: {e}")
            return False

    async def _start_ffmpeg(self) -> bool:
        """Start FFmpeg capture from /dev/video0"""
        try:
            command = [
                "ffmpeg",
                "-f", "v4l2",
                "-i", config.PI_CAMERA_DEVICE,
                "-c:v", "h264",
                "-b:v", str(config.PI_CAMERA_BITRATE),
                "-r", str(config.PI_CAMERA_FPS),
                "-s", f"{config.PI_CAMERA_WIDTH}x{config.PI_CAMERA_HEIGHT}",
                "-rtsp_transport", "tcp",
                f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}",
                "-loglevel", "error"
            ]

            self.ffmpeg_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=signal.signal(signal.SIGINT, signal.SIG_IGN)
            )

            await asyncio.sleep(1)
            if self.ffmpeg_process.poll() is not None:
                return False

            logger.info("  FFmpeg started")
            return True

        except Exception as e:
            logger.error(f"Failed to start FFmpeg: {e}")
            return False

    async def stop(self):
        """Stop FFmpeg and Mediamtx"""
        if self.ffmpeg_process:
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
            logger.info("  FFmpeg stopped")

        if self.mediamtx_process:
            self.mediamtx_process.terminate()
            try:
                self.mediamtx_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.mediamtx_process.kill()
            logger.info(" Mediamtx stopped")

        self.is_streaming = False

    def get_stream_info(self) -> Dict[str, Any]:
        """Get streaming information"""
        uptime = None
        if self.stream_start_time:
            uptime = (datetime.now() - self.stream_start_time).total_seconds()

        return {
            "is_streaming": self.is_streaming,
            "mediamtx_running": self.mediamtx_process and self.mediamtx_process.poll() is None,
            "ffmpeg_running": self.ffmpeg_process and self.ffmpeg_process.poll() is None,
            "uptime_seconds": uptime,
            "mediamtx_rtsp_url": f"rtsp://127.0.0.1:{config.MEDIAMTX_RTSP_PORT}/{config.MEDIAMTX_RTSP_PATH}",
        }

    def health_check(self) -> bool:
        """Check if both processes are running"""
        return (
            self.is_streaming and
            self.mediamtx_process and self.mediamtx_process.poll() is None and
            self.ffmpeg_process and self.ffmpeg_process.poll() is None
        )
