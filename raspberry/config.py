"""
Constantes de configuração para o Firmware do Robô AgroMotion
Versão de produção com integração real de hardware
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# CONFIGURAÇÃO DO ROBÔ
# ============================================================================
ROBOT_ID = os.getenv("ROBOT_ID", "agromotion-robot-01")
ROBOT_NAME = os.getenv("ROBOT_NAME", "Robot Agromotion")

# ============================================================================
# HARDWARE RASPBERRY PI
# ============================================================================
PI_CAMERA_WIDTH = 640
PI_CAMERA_HEIGHT = 480
PI_CAMERA_FPS = 30

# Sensor de temperatura (BCM2835 integrado)
TEMPERATURE_SENSOR_PATH = "/sys/class/thermal/thermal_zone0/temp"

# ============================================================================
# ARDUINO / COMUNICAÇÃO SÉRIE
# ============================================================================
ARDUINO_SERIAL_PORT = os.getenv("ARDUINO_SERIAL_PORT", "/dev/ttyACM0")
ARDUINO_BAUD_RATE = 115200

# Protocolos de mensagem para o Arduino
# Respostas esperadas do Arduino:
# GPS: {"type": "GPS", "lat": -31.234, "lon": 116.567, "alt": 100.5, "time": "14:23:45"}
# BATTERY: {"type": "BATTERY", "voltage": 12.5, "percentage": 85.0, "current": 1.2}
# ACK para comandos

# ============================================================================
# CONFIGURAÇÃO DO FIREBASE
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
# STREAMING DE VÍDEO (MEDIAMTX)
# ============================================================================
MEDIAMTX_RTSP_PORT = 8554  # Entrada do FFmpeg
MEDIAMTX_RTSP_PATH = "robot"  # rtsp://127.0.0.1:8554/robot

# Modo de fonte de vídeo: "camera" (padrão) ou "video" (ficheiro)
VIDEO_SOURCE_MODE = os.getenv("VIDEO_SOURCE_MODE", "camera")
# Caminho para o ficheiro de vídeo (usado quando VIDEO_SOURCE_MODE = "video")
VIDEO_FILE_PATH = os.getenv("VIDEO_FILE_PATH", "/home/pi/videos/sample.mp4")


# ============================================================================
# COMANDO E CONTROLO
# ============================================================================
WHEEL_MAX_SPEED = 255  # Valor PWM (0-255)

# Sensibilidade de movimento
MOVEMENT_DEADZONE = 0.1  # Ignorar valores abaixo de 10%

# ============================================================================
# TELEMETRIA
# ============================================================================
# Otimizado para menor uso de CPU
TELEMETRY_BROADCAST_INTERVAL = 2 # segundos (0.5 Hz) - Reduzido de 0.5s
TELEMETRY_FIREBASE_INTERVAL = 10.0  # Gravar no Firebase a cada 10 segundos - Reduzido de 5s
TELEMETRY_HISTORY_INTERVAL = 600.0 # 10 minutos
# ============================================================================
# BLOQUEIO DE CONTROLO
# ============================================================================
CONTROL_LOCK_TIMEOUT = 180  # Libertação automática após 3 minutos de inatividade

# ============================================================================
# REGISTOS E DEPURAÇÃO
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Ativar funcionalidades de depuração
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# ============================================================================
# DESEMPENHO E OTIMIZAÇÃO
# ============================================================================
# Gestão de memória
MEMORY_WARNING_THRESHOLD = 80  # percentagem
CPU_WARNING_THRESHOLD = 85  # percentagem


print(f" Configuração carregada para {ROBOT_NAME} ({ROBOT_ID})")
