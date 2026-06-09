import asyncio
import logging
import signal
import sys
from typing import Optional

import config
from system_monitor import SystemMonitor
from serial_handler import SerialHandler
from video_streaming import VideoStreamingManager
from command_handler import CommandHandler
from firebase_manager import FirebaseManager
from robot_mode_manager import RobotModeManager
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
        self.serial_handler = SerialHandler(
            config.ARDUINO_SERIAL_PORT,
            config.ARDUINO_BAUD_RATE
        )

        self.video_manager = None
        self.firebase_manager = FirebaseManager(self)
        self.command_handler = CommandHandler()
        self.mode_manager = RobotModeManager(self.serial_handler)
        self.telemetry_service = None

        # -----------------------------
        # CAMADA DE SINCRONIZAÇÃO DE CONTROLO
        # -----------------------------
        self._control_lock = asyncio.Lock()
        self._control_state = {
            "x": 0.0,
            "y": 0.0,
            "drum": 0.0,
            "ts": 0.0
        }

        self._dispatcher_task: Optional[asyncio.Task] = None

    # =========================================================
    # INICIALIZAÇÃO
    # =========================================================

    async def initialize(self) -> bool:
        logger.info(f"🚀 Iniciando firmware para: {config.ROBOT_NAME}...")

        if not self.firebase_manager.initialize():
            logger.error("Firebase falhou.")
            return False

        notif = self.firebase_manager.notification_service

        self.system_monitor.notification_service = notif
        self.serial_handler.notification_service = notif
        self.firebase_manager.on_auto_mode_change = self._on_auto_mode_change

        if not await self.serial_handler.connect():
            logger.warning("Arduino não detetado (modo simulação).")

        self.video_manager = VideoStreamingManager()
        if not await self.video_manager.start():
            logger.error("Falha no vídeo.")
            return False

        self.telemetry_service = TelemetryService(
            self.system_monitor,
            self.serial_handler
        )
        self.telemetry_service.on_telemetry_update = self._on_telemetry_update

        self.firebase_manager.on_control_change = self._on_control_change

        self.firebase_manager.start_listening()

        current_auto_mode = self.firebase_manager.get_current_auto_mode()
        if current_auto_mode is not None:
            await self.mode_manager.set_auto_mode(current_auto_mode, force=True)

        await self.telemetry_service.start()

        # INICIAR LOOP DO DISPATCHER (CORREÇÃO CHAVE)
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())

        return True

    # =========================================================
    # CALLBACKS DE CONTROLO (APENAS RECEÇÃO DE INPUT)
    # =========================================================

    async def execute_command(self, x: float, y: float, user: str):
        active_user = self.firebase_manager.access_manager.current_controller
        if user != active_user or self.mode_manager.auto_mode_enabled:
            return

        async with self._control_lock:
            self._control_state["x"] = float(x)
            self._control_state["y"] = float(y)
            self._control_state["ts"] = asyncio.get_event_loop().time()

    async def execute_drum(self, value: float, user: str):
        active_user = self.firebase_manager.access_manager.current_controller
        if user != active_user or self.mode_manager.auto_mode_enabled:
            return

        async with self._control_lock:
            self._control_state["drum"] = float(value)
            self._control_state["ts"] = asyncio.get_event_loop().time()

    # =========================================================
    # DISPATCHER CENTRAL
    # =========================================================

    async def _dispatch_loop(self):
        """
        Envia comandos ao Arduino de forma contínua e sincronizada.
        Isto elimina jitter entre joystick e drum.
        """
        last_sent = None

        while self.running:
            await asyncio.sleep(0.02)  # 50Hz (suave e estável)

            if self.mode_manager.auto_mode_enabled:
                last_sent = None
                continue

            async with self._control_lock:
                x = self._control_state["x"]
                y = self._control_state["y"]
                drum = self._control_state["drum"]
                ts = self._control_state["ts"]

            # evita spam se nada mudou
            snapshot = (x, y, drum)
            if snapshot == last_sent:
                continue

            last_sent = snapshot

            # -----------------------------
            # PROCESSAMENTO DO JOYSTICK
            # -----------------------------
            wheel_cmds = self.command_handler.process_joystick(
                x,
                y,
                config.WHEEL_MAX_SPEED
            )

            v_left = int(
                wheel_cmds["L"].speed *
                (1 if wheel_cmds["L"].direction == "FORWARD" else -1)
            )

            v_right = int(
                wheel_cmds["R"].speed *
                (1 if wheel_cmds["R"].direction == "FORWARD" else -1)
            )

            # -----------------------------
            # PROCESSAMENTO DO TAMBOR
            # -----------------------------
            drum_speed = int(drum * 255)

            # -----------------------------
            # PACOTE DE COMANDO DE SINCRONIZAÇÃO ÚNICA
            # -----------------------------
            command = {
                "cmd": "MIXED_CONTROL",
                "left": v_left,
                "right": v_right,
                "drum": drum_speed
            }

            try:
                await self.serial_handler.send_command(command)
            except Exception as e:
                logger.warning(f"Erro envio comando: {e}")

    # =========================================================
    # EVENTOS DE CONTROLO
    # =========================================================

    def _on_control_change(self, user_email: Optional[str], is_controlled: bool):
        status = "CONTROLADO" if is_controlled else "LIVRE"
        logger.info(f"📌 Controlo: {status} | {user_email}")

        if self.telemetry_service:
            asyncio.create_task(
                self.telemetry_service.update_robot_state(
                    controller_email=user_email
                )
            )

    def _on_telemetry_update(self, telemetry, save_history: bool = False):
        asyncio.create_task(
            self.firebase_manager.save_telemetry(
                telemetry.to_dict(),
                save_history=save_history
            )
        )

    def _on_auto_mode_change(self, enabled: bool, force: bool = False):
        asyncio.create_task(self.mode_manager.set_auto_mode(enabled, force=force))

    # =========================================================
    # LOOP PRINCIPAL
    # =========================================================

    async def run(self):
        self.running = True
        logger.info("✅ Firmware ativo. A aguardar comandos...")

        while self.running:
            await asyncio.sleep(1.0)

    # =========================================================
    # ENCERRAMENTO
    # =========================================================

    async def shutdown(self):
        if not self.running:
            return

        logger.info("🛑 Shutdown...")

        self.running = False

        if self._dispatcher_task:
            self._dispatcher_task.cancel()

        if self.serial_handler:
            await self.serial_handler.send_stop_command()
            await asyncio.sleep(0.2)
            await self.serial_handler.disconnect()

        if self.video_manager:
            await self.video_manager.stop()

        if self.firebase_manager:
            await self.firebase_manager.disconnect()

        logger.info("🏁 Encerrado.")
        sys.exit(0)


# =========================================================
# PRINCIPAL
# =========================================================

async def main():
    firmware = RobotFirmware()
    loop = asyncio.get_running_loop()

    def signal_handler():
        asyncio.create_task(firmware.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        if await firmware.initialize():
            await firmware.run()
    except Exception as e:
        logger.critical(f"FATAL: {e}", exc_info=True)
    finally:
        if firmware.running:
            await firmware.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
