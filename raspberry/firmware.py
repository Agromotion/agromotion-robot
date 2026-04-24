import asyncio
import logging
import signal
import sys
from typing import Optional
from datetime import datetime

import config
from system_monitor import SystemMonitor
from serial_handler import SerialHandler
from video_streaming import VideoStreamingManager
from command_handler import CommandHandler
from firebase_manager import FirebaseManager
from telemetry_service import TelemetryService

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
        logger.info(f"🚀 Iniciando firmware para o robô: {config.ROBOT_NAME}...")

        # 1. Inicializar Firebase (Reset de estados e Fila)
        if not self.firebase_manager.initialize():
            logger.error("Falha Crítica: Não foi possível iniciar o Firebase Manager.")
            return False

        notif = self.firebase_manager.notification_service

        # 2. Injetar serviço de notificações
        self.system_monitor.notification_service = notif
        self.serial_handler.notification_service = notif

        # 3. Conectar ao Arduino
        if not await self.serial_handler.connect():
            logger.warning("⚠️ Aviso: Arduino não detectado. O robô funcionará apenas em modo simulação de vídeo.")

        # 4. Inicializar Streaming de Vídeo (Pipeline rpicam + ffmpeg)
        self.video_manager = VideoStreamingManager()
        if not await self.video_manager.start():
            logger.error("Falha Crítica: Falha ao iniciar o streaming da câmara.")
            return False
        
        self.firebase_manager.start_listening()

        # 5. Inicializar Serviço de Telemetria
        self.telemetry_service = TelemetryService(self.system_monitor, self.serial_handler)
        self.telemetry_service.on_telemetry_update = self._on_telemetry_update

        # 6. Configurar Callbacks de Controlo
        self.firebase_manager.on_control_change = self._on_control_change

        await self.telemetry_service.start()
        return True

    def _on_control_change(self, user_email: Optional[str], is_controlled: bool):
        """Callback disparado quando a fila FIFO promove um novo condutor."""
        status = "CONTROLADO" if is_controlled else "LIVRE"
        logger.info(f"📌 Estado de Controlo: {status} | Utilizador: {user_email}")
        
        if self.telemetry_service:
            asyncio.create_task(
                self.telemetry_service.update_robot_state(controller_email=user_email)
            )

    async def execute_command(self, x: float, y: float, user: str):
        """Executa comandos de movimento vindos do DataChannel via FirebaseManager."""
        
        # SEGURANÇA: Verifica se o utilizador que enviou a mensagem é o controlador ativo
        active_user = self.firebase_manager.access_manager.current_controller
        if user != active_user:
            logger.warning(f"🚫 Comando ignorado: Utilizador {user} tentou mover o robô, mas o controlo pertence a {active_user}.")
            return

        # Processa as velocidades para os motores
        wheel_cmds = self.command_handler.process_joystick(x, y, config.WHEEL_MAX_SPEED)

        is_moving = (x != 0 or y != 0)
        rotation = "NONE"
        if x > 0: rotation = "CW"
        elif x < 0: rotation = "CCW"

        # Atualiza estado interno para telemetria
        if self.telemetry_service:
            asyncio.create_task(
                self.telemetry_service.update_robot_state(moving=is_moving, rotation=rotation)
            )

        # Envia para o Arduino
        v_left = int(wheel_cmds["L"].speed * (1 if wheel_cmds["L"].direction == "FORWARD" else -1))
        v_right = int(wheel_cmds["R"].speed * (1 if wheel_cmds["R"].direction == "FORWARD" else -1))

        await self.serial_handler.send_move_command(v_left, v_right)

    def _on_telemetry_update(self, telemetry, save_history: bool = False):
        """Envia os dados recolhidos para o Firestore."""
        asyncio.create_task(
            self.firebase_manager.save_telemetry(telemetry.to_dict(), save_history=save_history)
        )

    async def run(self):
        """Loop principal de execução."""
        self.running = True
        logger.info("✅ Firmware em plena execução. Aguardando comandos da App...")
        while self.running:
            # Aqui podes adicionar verificações de rotina se necessário
            await asyncio.sleep(1.0)

    async def shutdown(self):
        """Encerramento seguro do robô."""
        if not self.running:
            return

        logger.info("🛑 Iniciando shutdown seguro...")
        self.running = False

        # 1. Parar motores imediatamente
        if self.serial_handler:
            logger.info("Stopping motors...")
            await self.serial_handler.send_stop_command()
            await asyncio.sleep(0.5)
            await self.serial_handler.disconnect()

        # 2. Fechar vídeo
        if self.video_manager:
            await self.video_manager.stop()

        # 3. Desligar Firebase e libertar fila
        if self.firebase_manager:
            await self.firebase_manager.disconnect()

        logger.info("🏁 Firmware encerrado com sucesso.")
        # Usamos o sys.exit aqui se for chamado por sinal, ou apenas deixamos o loop main terminar
        sys.exit(0)

async def main():
    firmware = RobotFirmware()
    loop = asyncio.get_running_loop()

    # Gestão de Sinais (CTRL+C ou SIGTERM)
    def signal_handler():
        logger.warning("Sinal de interrupção recebido.")
        asyncio.create_task(firmware.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        if await firmware.initialize():
            await firmware.run()
    except Exception as e:
        logger.critical(f"💥 Erro fatal não tratado: {e}", exc_info=True)
    finally:
        if firmware.running:
            await firmware.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass