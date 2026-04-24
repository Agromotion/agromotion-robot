"""
Configuration constants for AgroMotion Robot Firmware
Production version with real hardware integration
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# ROBOT CONFIGURATION
# ============================================================================
ROBOT_ID = os.getenv("ROBOT_ID", "agromotion-robot-01")
ROBOT_NAME = os.getenv("ROBOT_NAME", "Robot Agromotion")

# ============================================================================
# RASPBERRY PI HARDWARE
# ============================================================================
PI_CAMERA_WIDTH = 1280
PI_CAMERA_HEIGHT = 720
PI_CAMERA_FPS = 30

# Temperature sensor (BCM2835 on-chip)
TEMPERATURE_SENSOR_PATH = "/sys/class/thermal/thermal_zone0/temp"

# ============================================================================
# ARDUINO / SERIAL COMMUNICATION
# ============================================================================
ARDUINO_SERIAL_PORT = os.getenv("ARDUINO_SERIAL_PORT", "/dev/ttyUSB0")
ARDUINO_BAUD_RATE = 115200

# Message protocols to Arduino
# Expected Arduino responses:
# GPS: {"type": "GPS", "lat": -31.234, "lon": 116.567, "alt": 100.5, "time": "14:23:45"}
# BATTERY: {"type": "BATTERY", "voltage": 12.5, "percentage": 85.0, "current": 1.2}
# ACK for commands

# ============================================================================
# FIREBASE CONFIGURATION
# ============================================================================
FIREBASE_CREDENTIALS_PATH = os.getenv(
    "FIREBASE_CREDENTIALS_PATH",
    "/home/pi/raspberry/secrets.json"
)
FIREBASE_DATABASE_URL = os.getenv(
    "FIREBASE_DATABASE_URL",
    "https://agromotion-default.firebaseio.com"
)
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "agromotion")

# ============================================================================
# VIDEO STREAMING (MEDIAMTX)
# ============================================================================
MEDIAMTX_RTSP_PORT = 8554  # Input from FFmpeg
MEDIAMTX_RTSP_PATH = "robot"  # rtsp://127.0.0.1:8554/robot

# Video source mode: "camera" (default) or "video" (file)
VIDEO_SOURCE_MODE = os.getenv("VIDEO_SOURCE_MODE", "camera")
# Path to video file (used when VIDEO_SOURCE_MODE = "video")
VIDEO_FILE_PATH = os.getenv("VIDEO_FILE_PATH", "/home/pi/videos/sample.mp4")


# ============================================================================
# COMMAND & CONTROL
# ============================================================================
WHEEL_MAX_SPEED = 255  # PWM value (0-255)

# Movement sensitivity
MOVEMENT_DEADZONE = 0.1  # Ignore values below 10%

# ============================================================================
# TELEMETRY
# ============================================================================
# Optimized for lower CPU usage
TELEMETRY_BROADCAST_INTERVAL = 2 # seconds (0.5 Hz) - Reduced from 0.5s
TELEMETRY_FIREBASE_INTERVAL = 10.0  # Save to Firebase every 10 seconds - Reduced from 5s

# ============================================================================
# CONTROL LOCKING
# ============================================================================
CONTROL_LOCK_TIMEOUT = 180  # Auto-release after 3 minutes of inactivity

# ============================================================================
# LOGGING & DEBUG
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Enable debug features
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# ============================================================================
# PERFORMANCE & OPTIMIZATION
# ============================================================================
# Memory management
MEMORY_WARNING_THRESHOLD = 80  # percentage
CPU_WARNING_THRESHOLD = 85  # percentage


print(f" Configuration loaded for {ROBOT_NAME} ({ROBOT_ID})")
