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
PI_CAMERA_DEVICE = "/dev/video0"  # Main camera device
PI_CAMERA_WIDTH = 1280
PI_CAMERA_HEIGHT = 720
PI_CAMERA_FPS = 30
PI_CAMERA_BITRATE = 2000000  # 2Mbps for efficient streaming

# Temperature sensor (BCM2835 on-chip)
TEMPERATURE_SENSOR_PATH = "/sys/class/thermal/thermal_zone0/temp"

# ============================================================================
# ARDUINO / SERIAL COMMUNICATION
# ============================================================================
ARDUINO_SERIAL_PORT = os.getenv("ARDUINO_SERIAL_PORT", "/dev/ttyUSB0")
ARDUINO_BAUD_RATE = 115200
ARDUINO_TIMEOUT = 1.0  # seconds
ARDUINO_READ_INTERVAL = 0.1  # seconds

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
MEDIAMTX_WEBRTC_PORT = 8555  # Output to clients
MEDIAMTX_RTSP_PATH = "robot"  # rtsp://127.0.0.1:8554/robot
MEDIAMTX_CONFIG_PATH = "/home/pi/mediamtx.yml"  # Optional, will use defaults if not found

# Video source mode: "camera" (default) or "video" (file)
VIDEO_SOURCE_MODE = os.getenv("VIDEO_SOURCE_MODE", "camera")
# Path to video file (used when VIDEO_SOURCE_MODE = "video")
VIDEO_FILE_PATH = os.getenv("VIDEO_FILE_PATH", "/home/pi/videos/sample.mp4")

# Max simultaneous video clients
MAX_VIDEO_CLIENTS = 4

# ============================================================================
# COMMAND & CONTROL
# ============================================================================
# 3-wheel rover configuration
# Wheel positions:
#   FL (front-left)   FR (front-right)
#        \              /
#         \            /
#          \          /
#           \        /
#            \      /
#             \    /
#              \  /
#              REAR

WHEEL_NAMES = ["FL", "FR", "REAR"]  # Front-left, Front-right, Rear
WHEEL_MAX_SPEED = 255  # PWM value (0-255)
WHEEL_MIN_SPEED = 0

# Joystick ranges
JOYSTICK_X_MIN = -1.0  # Left
JOYSTICK_X_MAX = 1.0   # Right
JOYSTICK_Y_MIN = -1.0  # Backward
JOYSTICK_Y_MAX = 1.0   # Forward

# Movement sensitivity
MOVEMENT_DEADZONE = 0.1  # Ignore values below 10%
MOVEMENT_DAMPING = 0.8   # Smooth acceleration factor

# ============================================================================
# TELEMETRY
# ============================================================================
# Optimized for lower CPU usage
TELEMETRY_BROADCAST_INTERVAL = 2.0  # seconds (0.5 Hz) - Reduced from 0.5s
TELEMETRY_FIREBASE_INTERVAL = 10.0  # Save to Firebase every 10 seconds - Reduced from 5s
TELEMETRY_HISTORY_RETENTION = 86400  # 24 hours in seconds


# ============================================================================
# WEBSOCKET (Commands & Telemetry)
# ============================================================================
WEBSOCKET_HOST = "0.0.0.0"
WEBSOCKET_PORT = 8888
WEBSOCKET_KEEPALIVE = 30  # seconds

# ============================================================================
# CONTROL LOCKING
# ============================================================================
CONTROL_LOCK_TIMEOUT = 180  # Auto-release after 3 minutes of inactivity
CONTROL_LOCK_ACTIVITY_INTERVAL = 10  # Send heartbeat every 10 seconds

# ============================================================================
# LOGGING & DEBUG
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = "/home/agromotion/logs"
LOG_FILE_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Enable debug features
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
DEBUG_SERIAL = DEBUG_MODE  # Log all serial communication
DEBUG_FIREBASE = DEBUG_MODE  # Log all Firebase operations
DEBUG_COMMANDS = DEBUG_MODE  # Log all commands sent to wheels

# ============================================================================
# PERFORMANCE & OPTIMIZATION
# ============================================================================
# Python asyncio settings
MAX_CONCURRENT_TASKS = 20
TASK_TIMEOUT = 30  # seconds

# Firebase batch settings
FIREBASE_BATCH_SIZE = 10
FIREBASE_BATCH_TIMEOUT = 5  # seconds

# Memory management
MEMORY_WARNING_THRESHOLD = 80  # percentage
CPU_WARNING_THRESHOLD = 85  # percentage

# ============================================================================
# MAINTENANCE & MONITORING
# ============================================================================
ENABLE_HEALTH_CHECK = True
HEALTH_CHECK_INTERVAL = 60  # seconds

ENABLE_METRICS = True
METRICS_PORT = 9090
METRICS_INTERVAL = 30  # seconds

# ============================================================================
# TIMEOUTS & RETRIES
# ============================================================================
FIREBASE_TIMEOUT = 10  # seconds
FIREBASE_MAX_RETRIES = 3
FIREBASE_RETRY_DELAY = 1  # seconds

SERIAL_TIMEOUT = 5  # seconds
SERIAL_MAX_RETRIES = 3
SERIAL_RETRY_DELAY = 0.5  # seconds

WEBSOCKET_TIMEOUT = 60  # seconds
WEBSOCKET_PING_INTERVAL = 30  # seconds

# ============================================================================
# DEVICE PATHS (Linux/Raspberry Pi specific)
# ============================================================================
DEVICES = {
    "camera": PI_CAMERA_DEVICE,
    "thermal": TEMPERATURE_SENSOR_PATH,
    "serial": ARDUINO_SERIAL_PORT,
}

# ============================================================================
# FEATURES
# ============================================================================
FEATURE_GPS = True
FEATURE_BATTERY_MONITORING = True
FEATURE_SYSTEM_MONITORING = True
FEATURE_TELEMETRY_LOGGING = True
FEATURE_FIREBASE_SYNC = True
FEATURE_MEDIAMTX_STREAMING = True
FEATURE_ARDUINO_CONTROL = True

print(f" Configuration loaded for {ROBOT_NAME} ({ROBOT_ID})")
