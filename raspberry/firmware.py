import asyncio
import logging
import signal
import sys
from typing import Optional, Dict, Any
from datetime import datetime

import config
from system_monitor import SystemMonitor
from serial_handler import SerialHandler
from video_streaming import VideoStreamingManager
from command_handler import CommandHandler
from firebase_manager import FirebaseManager
from telemetry_service import TelemetryService

logger = logging.getLogger(__name__)

class RobotFirmware:
    def __init__(self):
        self.robot_id = config.ROBOT_ID
        self.running = False
        self.system_monitor = SystemMonitor()
        self.serial_handler = SerialHandler(config.ARDUINO_SERIAL_PORT, config.ARDUINO_BAUD_RATE)
        self.video_manager = None
        self.firebase_manager = FirebaseManager(self)
        self.command_handler = CommandHandler()
        self.telemetry_service = None

    async def initialize(self) -> bool:
        logger.info(f"Iniciando firmware {config.ROBOT_NAME}...")
        
        # 1. Initialize Firebase
        if not await self.firebase_manager.initialize():
            logger.error("Falha ao iniciar Firebase Manager.")
            return False
            
        notif = self.firebase_manager.notification_service
        
        # 2. Inject Notification Service
        self.system_monitor.notification_service = notif
        self.serial_handler.notification_service = notif
        
        # 3. Connect to Arduino
        if not await self.serial_handler.connect():
            logger.warning("Falha ao conectar ao Arduino. Verifique a porta serial.")
        
        # 4. Initialize Video
        self.video_manager = VideoStreamingManager(mode="camera")
        if not await self.video_manager.start():
            logger.error("Falha ao iniciar streaming de vídeo.")
            return False
        
        # 5. Initialize Telemetry Loop
        self.telemetry_service = TelemetryService(self.system_monitor, self.serial_handler)
        self.telemetry_service.on_telemetry_update = self._on_telemetry_update
        
        # --- NOVO: Configurar callback para mudanças de controlo ---
        self.firebase_manager.on_control_change = self._on_control_change
        
        await self.telemetry_service.start()
        return True

    # --- NOVO: Atualiza a telemetria quando o controlador muda ---
    def _on_control_change(self, user_email: Optional[str], is_controlled: bool):
        """Callback disparado pelo FirebaseManager quando o lock de controlo muda."""
        if self.telemetry_service:
            asyncio.create_task(
                self.telemetry_service.update_robot_state(controller_email=user_email)
            )

    async def execute_command(self, x: float, y: float, user: str):
        """Executa o comando e avisa a telemetria que o robô está em movimento."""
        wheel_cmds = self.command_handler.process_joystick(x, y, config.WHEEL_MAX_SPEED)
        
        # Determinar se o robô está parado ou a mexer
        is_moving = (x != 0 or y != 0)
        
        # Determinar direção de rotação para a telemetria
        rotation = "NONE"
        if x > 0: rotation = "CW"
        elif x < 0: rotation = "CCW"

        # Atualizar estado na telemetria (sem bloquear o movimento)
        if self.telemetry_service:
            asyncio.create_task(
                self.telemetry_service.update_robot_state(moving=is_moving, rotation=rotation)
            )

        # Enviar para o Arduino
        # Nota: Multiplicamos a velocidade pela direção (1 ou -1) para suportar o teu Arduino bidirecional
        await self.serial_handler.send_move_command(
            wheel_cmds["FL"].speed * (1 if wheel_cmds["FL"].direction == "FORWARD" else -1),
            wheel_cmds["FR"].speed * (1 if wheel_cmds["FR"].direction == "FORWARD" else -1),
            wheel_cmds["REAR"].speed * (1 if wheel_cmds["REAR"].direction == "FORWARD" else -1)
        )

    def _on_telemetry_update(self, telemetry):
        """Push telemetry updates to Firestore."""
        asyncio.create_task(self.firebase_manager.save_telemetry(telemetry.to_dict()))

    async def run(self):
        self.running = True
        logger.info("Firmware em execução.")
        while self.running:
            await asyncio.sleep(1.0)

    async def shutdown(self):
        logger.info("Encerrando firmware...")
        self.running = False
        if self.serial_handler:
            await self.serial_handler.send_stop_command()
        if self.video_manager:
            await self.video_manager.stop()
        if self.firebase_manager:
            await self.firebase_manager.disconnect()
        logger.info("Firmware encerrado com segurança.")

# ... (main mantem-se igual)