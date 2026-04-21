"""
Command handler simplificado para robô diferencial (2 canais: L e R)
Converte input do joystick para velocidades de motor Esquerda/Direita.
"""

import logging
from typing import Dict
from dataclasses import dataclass
from datetime import datetime
import config

logger = logging.getLogger(__name__)

@dataclass
class WheelCommand:
    speed: int  # 0-255
    direction: str  # "FORWARD" ou "REVERSE"

class CommandHandler:
    def __init__(self):
        self.deadzone = config.MOVEMENT_DEADZONE # ex: 0.1

    def process_joystick(self, x: float, y: float, max_speed: int = 255) -> Dict:
        """
        Lógica Diferencial (Arcade Drive):
        y = avanço/recuo
        x = rotação
        """
        # Aplicar Deadzone
        if abs(x) < self.deadzone: x = 0
        if abs(y) < self.deadzone: y = 0

        # Calcular velocidades base
        left = y + x
        right = y - x

        # Normalizar para não exceder 1.0 ou -1.0
        max_val = max(abs(left), abs(right), 1.0)
        left /= max_val
        right /= max_val

        return {
            "L": self._create_wheel_cmd(left * max_speed),
            "R": self._create_wheel_cmd(right * max_speed)
        }

    def _create_wheel_cmd(self, raw_speed: float) -> WheelCommand:
        speed = int(abs(raw_speed))
        direction = "FORWARD" if raw_speed >= 0 else "REVERSE"
        return WheelCommand(speed=min(255, speed), direction=direction)