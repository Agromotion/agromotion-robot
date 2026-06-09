import logging

from serial_handler import SerialHandler

logger = logging.getLogger(__name__)


class RobotModeManager:
    def __init__(self, serial_handler: SerialHandler):
        self.serial_handler = serial_handler
        self.auto_mode_enabled = False

    async def set_auto_mode(self, enabled: bool, force: bool = False):
        enabled = bool(enabled)
        if enabled == self.auto_mode_enabled and not force:
            return

        self.auto_mode_enabled = enabled

        if await self.serial_handler.send_auto_mode_command(enabled):
            logger.info(f"AUTO_MODE enviado ao Arduino: {'ON' if enabled else 'OFF'}")
        else:
            logger.warning("Não foi possível enviar AUTO_MODE ao Arduino.")

        await self.serial_handler.send_stop_command()
