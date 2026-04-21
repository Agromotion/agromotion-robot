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

# Configuração de Logging para debug no terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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
        """Inicializa todos os subsistemas do robô."""
        logger.info(f"Iniciando firmware para o robô: {config.ROBOT_NAME}...")
        
        # 1. Inicializar Firebase (Comunicação com a App)
        if not await self.firebase_manager.initialize():
            logger.error("Falha: Não foi possível iniciar o Firebase Manager.")
            return False
            
        notif = self.firebase_manager.notification_service
        
        # 2. Injetar serviço de notificações nos monitores
        self.system_monitor.notification_service = notif
        self.serial_handler.notification_service = notif
        
        # 3. Conectar ao Arduino via Serial
        if not await self.serial_handler.connect():
            logger.warning("Aviso: Arduino não detectado na porta especificada.")
        
        # 4. Inicializar Streaming de Vídeo (Câmara Real)
        self.video_manager = VideoStreamingManager()
        if not await self.video_manager.start():
            logger.error("Erro: Falha ao iniciar o streaming da câmara.")
            return False
        
        # 5. Inicializar Serviço de Telemetria (GPS, Bateria, CPU)
        self.telemetry_service = TelemetryService(self.system_monitor, self.serial_handler)
        self.telemetry_service.on_telemetry_update = self._on_telemetry_update
        
        # 6. Configurar Callbacks de Controlo
        self.firebase_manager.on_control_change = self._on_control_change
        
        # Iniciar o loop de telemetria em background
        await self.telemetry_service.start()
        
        return True

    def _on_control_change(self, user_email: Optional[str], is_controlled: bool):
        """Disparado quando alguém assume ou larga o controlo do robô na App."""
        logger.info(f"Controlo alterado: {user_email} (Ativo: {is_controlled})")
        if self.telemetry_service:
            asyncio.create_task(
                self.telemetry_service.update_robot_state(controller_email=user_email)
            )

    async def execute_command(self, x: float, y: float, user: str):
        """
        Traduz o input do Joystick (X, Y) para comandos de motor.
        Usa o CommandHandler para mixagem diferencial (L/R).
        """
        # O CommandHandler calcula as velocidades L e R baseadas no X e Y
        wheel_cmds = self.command_handler.process_joystick(x, y, config.WHEEL_MAX_SPEED)

        # Determinar estado de movimento para telemetria
        is_moving = (x != 0 or y != 0)
        rotation = "NONE"
        if x > 0: rotation = "CW"   # Clockwise
        elif x < 0: rotation = "CCW" # Counter-Clockwise

        if self.telemetry_service:
            asyncio.create_task(
                self.telemetry_service.update_robot_state(moving=is_moving, rotation=rotation)
            )

        # Conversão para o protocolo do Arduino: Velocidade * Direção
        # Se direção for REVERSE, o valor enviado será negativo (ex: -255) para a ponte-H
        v_left = int(wheel_cmds["L"].speed * (1 if wheel_cmds["L"].direction == "FORWARD" else -1))
        v_right = int(wheel_cmds["R"].speed * (1 if wheel_cmds["R"].direction == "FORWARD" else -1))

        # Enviar comando via SerialHandler (Chaves L e R)
        await self.serial_handler.send_move_command(v_left, v_right)

    def _on_telemetry_update(self, telemetry):
        """Envia os dados recolhidos do Arduino e Sistema para o Firebase."""
        asyncio.create_task(self.firebase_manager.save_telemetry(telemetry.to_dict()))

    async def run(self):
        """Mantém o script principal vivo."""
        self.running = True
        logger.info("Firmware em plena execução. Aguardando comandos da App...")
        while self.running:
            await asyncio.sleep(1.0)

    async def shutdown(self):
        """Encerra todos os processos e para o robô imediatamente."""
        if not self.running:
            return
            
        logger.info("A iniciar shutdown seguro...")
        self.running = False
        
        # 1. Parar motores imediatamente
        if self.serial_handler:
            logger.info("A enviar comando STOP para os motores.")
            await self.serial_handler.send_stop_command()
            await asyncio.sleep(0.2)
            await self.serial_handler.disconnect()
            
        # 2. Parar vídeo
        if self.video_manager:
            await self.video_manager.stop()
            
        # 3. Desligar Firebase
        if self.firebase_manager:
            await self.firebase_manager.disconnect()
            
        logger.info("Firmware encerrado.")
        sys.exit(0)

# --- Entrada do Firmware ---
async def main():
    firmware = RobotFirmware()
    
    # Capturar sinais do sistema para parar o robô com Ctrl+C
    loop = asyncio.get_running_loop()
    
    def signal_handler():
        logger.warning("Sinal de interrupção recebido.")
        asyncio.create_task(firmware.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        if await firmware.initialize():
            await firmware.run()
    except Exception as e:
        logger.critical(f"Erro fatal não tratado: {e}", exc_info=True)
    finally:
        if firmware.running:
            await firmware.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass