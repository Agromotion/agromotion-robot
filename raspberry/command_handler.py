"""
Command handler for 3-wheel rover with triangle configuration
Converts joystick input to individual wheel speeds

Wheel layout:
    FL (front-left)    FR (front-right)
         \              /
          \            /
           \          /
            \        /
             \      /
              \    /
              REAR

Movement scenarios:
- Forward: FL+FR forward, REAR forward
- Backward: FL+FR backward, REAR backward  
- Turn left: FL backward, FR forward, REAR stationary
- Turn right: FL forward, FR backward, REAR stationary
- Rotate CCW: FL backward, FR forward, REAR forward
- Rotate CW: FL forward, FR backward, REAR backward
- Strafe left: FL forward, FR backward, REAR left
- Strafe right: FL backward, FR forward, REAR right
"""

import logging
import math
from typing import Dict, Tuple
from datetime import datetime
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)


@dataclass
class WheelCommand:
    """Command for a single wheel"""
    name: str  # "FL", "FR", "REAR"
    speed: int  # 0-255 (PWM value)
    direction: str  # "FORWARD", "BACKWARD", "STOP"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "speed": self.speed,
            "direction": self.direction,
            "timestamp": self.timestamp
        }


class CommandHandler:
    """
    Convert joystick input to wheel commands
    
    Input: joystick_x (-1 to 1), joystick_y (-1 to 1)
    Output: wheel speeds for FL, FR, REAR
    """

    def __init__(self):
        self.last_command = None
        self.command_history = []
        self.max_history = 100
        
        # Deadzone and damping
        self.deadzone = config.MOVEMENT_DEADZONE
        self.damping = config.MOVEMENT_DAMPING
        
        # Speed calibration (can be tuned)
        self.forward_speed_modifier = 1.0
        self.turn_speed_modifier = 1.0
        self.strafe_speed_modifier = 0.8  # Strafe is slower

    def process_joystick(
        self,
        joystick_x: float,
        joystick_y: float,
        max_speed: int = 255
    ) -> Dict[str, WheelCommand]:
        """
        Process joystick input and return wheel commands
        
        Args:
            joystick_x: -1 (left) to 1 (right)
            joystick_y: -1 (backward) to 1 (forward)
            max_speed: Maximum speed (0-255)
        
        Returns:
            Dict with keys "FL", "FR", "REAR" containing WheelCommand objects
        """
        
        # Apply deadzone
        if abs(joystick_x) < self.deadzone:
            joystick_x = 0.0
        if abs(joystick_y) < self.deadzone:
            joystick_y = 0.0

        # Determine movement type and calculate speeds
        if joystick_x == 0 and joystick_y == 0:
            # No input - stop
            commands = self._stop_all()
        elif joystick_x == 0:
            # Pure forward/backward
            commands = self._forward_backward(joystick_y, max_speed)
        elif joystick_y == 0:
            # Pure rotation/strafe
            commands = self._rotate(joystick_x, max_speed)
        else:
            # Combined movement - calculate best motion
            magnitude = math.sqrt(joystick_x ** 2 + joystick_y ** 2)
            angle = math.atan2(joystick_x, joystick_y)
            commands = self._combined_movement(angle, magnitude, max_speed)

        # Store in history
        self.last_command = commands
        self.command_history.append(commands)
        if len(self.command_history) > self.max_history:
            self.command_history.pop(0)

        # Log command
        self._log_command(commands)

        return commands

    def _forward_backward(
        self,
        forward_input: float,
        max_speed: int
    ) -> Dict[str, WheelCommand]:
        """Handle pure forward/backward movement"""
        
        speed = int(abs(forward_input) * max_speed * self.forward_speed_modifier)
        speed = max(0, min(max_speed, speed))
        
        if forward_input > 0:
            # Forward
            direction = "FORWARD"
        else:
            # Backward
            direction = "BACKWARD"

        return {
            "FL": WheelCommand("FL", speed, direction),
            "FR": WheelCommand("FR", speed, direction),
            "REAR": WheelCommand("REAR", speed, direction),
        }

    def _rotate(
        self,
        rotation_input: float,
        max_speed: int
    ) -> Dict[str, WheelCommand]:
        """
        Handle rotation (in place)
        
        Left (negative joystick_x): Rotate CCW
        Right (positive joystick_x): Rotate CW
        """
        
        speed = int(abs(rotation_input) * max_speed * self.turn_speed_modifier)
        speed = max(0, min(max_speed, speed))

        if rotation_input < 0:
            # Rotate counter-clockwise: FL backward, FR forward
            return {
                "FL": WheelCommand("FL", speed, "BACKWARD"),
                "FR": WheelCommand("FR", speed, "FORWARD"),
                "REAR": WheelCommand("REAR", 0, "STOP"),
            }
        else:
            # Rotate clockwise: FL forward, FR backward
            return {
                "FL": WheelCommand("FL", speed, "FORWARD"),
                "FR": WheelCommand("FR", speed, "BACKWARD"),
                "REAR": WheelCommand("REAR", 0, "STOP"),
            }

    def _combined_movement(
        self,
        angle: float,
        magnitude: float,
        max_speed: int
    ) -> Dict[str, WheelCommand]:
        """
        Handle combined movement (forward + rotation)
        
        angle is calculated as atan2(x, y) where:
        - angle = 0: forward
        - angle = π/2: right
        - angle = -π/2: left
        - angle = ±π: backward
        """
        
        base_speed = int(magnitude * max_speed)
        base_speed = max(0, min(max_speed, base_speed))

        # Determine primary direction
        if -math.pi / 4 <= angle <= math.pi / 4:
            # Mostly forward (±45°)
            return self._forward_biased(angle, base_speed, max_speed)
        elif math.pi / 4 < angle <= 3 * math.pi / 4:
            # Mostly right (+45° to +135°)
            return self._right_biased(angle, base_speed, max_speed)
        elif -3 * math.pi / 4 <= angle < -math.pi / 4:
            # Mostly left (-135° to -45°)
            return self._left_biased(angle, base_speed, max_speed)
        else:
            # Mostly backward (±135° to ±180°)
            return self._backward_biased(angle, base_speed, max_speed)

    def _forward_biased(
        self,
        angle: float,
        base_speed: int,
        max_speed: int
    ) -> Dict[str, WheelCommand]:
        """Forward-biased movement with rotation correction"""
        
        # Normalize angle to -π/4 to π/4
        turn_factor = angle / (math.pi / 4)  # -1 to 1
        
        if turn_factor > 0:
            # Turning right
            fl_speed = base_speed
            fr_speed = int(base_speed * (1 - abs(turn_factor) * 0.5))
            rear_speed = base_speed
        else:
            # Turning left
            fl_speed = int(base_speed * (1 - abs(turn_factor) * 0.5))
            fr_speed = base_speed
            rear_speed = base_speed

        return {
            "FL": WheelCommand("FL", min(max_speed, fl_speed), "FORWARD"),
            "FR": WheelCommand("FR", min(max_speed, fr_speed), "FORWARD"),
            "REAR": WheelCommand("REAR", min(max_speed, rear_speed), "FORWARD"),
        }

    def _backward_biased(
        self,
        angle: float,
        base_speed: int,
        max_speed: int
    ) -> Dict[str, WheelCommand]:
        """Backward-biased movement"""
        
        turn_factor = abs(angle) - (3 * math.pi / 4)
        turn_factor = min(1.0, turn_factor / (math.pi / 4))

        if angle > 0:
            # Backward-right
            fl_speed = base_speed
            fr_speed = int(base_speed * (1 - turn_factor * 0.5))
        else:
            # Backward-left
            fl_speed = int(base_speed * (1 - turn_factor * 0.5))
            fr_speed = base_speed

        return {
            "FL": WheelCommand("FL", min(max_speed, fl_speed), "BACKWARD"),
            "FR": WheelCommand("FR", min(max_speed, fr_speed), "BACKWARD"),
            "REAR": WheelCommand("REAR", min(max_speed, base_speed), "BACKWARD"),
        }

    def _left_biased(
        self,
        angle: float,
        base_speed: int,
        max_speed: int
    ) -> Dict[str, WheelCommand]:
        """Left-biased movement"""
        
        # Angle between -3π/4 and -π/4
        # Forward is at -π/2, backward is at -3π/4 and -π/4
        move_forward = angle > -math.pi / 2
        
        if move_forward:
            # Left-forward
            return {
                "FL": WheelCommand("FL", 0, "STOP"),
                "FR": WheelCommand("FR", min(max_speed, base_speed), "FORWARD"),
                "REAR": WheelCommand("REAR", 0, "STOP"),
            }
        else:
            # Left-backward
            return {
                "FL": WheelCommand("FL", min(max_speed, base_speed), "BACKWARD"),
                "FR": WheelCommand("FR", 0, "STOP"),
                "REAR": WheelCommand("REAR", 0, "STOP"),
            }

    def _right_biased(
        self,
        angle: float,
        base_speed: int,
        max_speed: int
    ) -> Dict[str, WheelCommand]:
        """Right-biased movement"""
        
        move_forward = angle < math.pi / 2

        if move_forward:
            # Right-forward
            return {
                "FL": WheelCommand("FL", min(max_speed, base_speed), "FORWARD"),
                "FR": WheelCommand("FR", 0, "STOP"),
                "REAR": WheelCommand("REAR", 0, "STOP"),
            }
        else:
            # Right-backward
            return {
                "FL": WheelCommand("FL", 0, "STOP"),
                "FR": WheelCommand("FR", min(max_speed, base_speed), "BACKWARD"),
                "REAR": WheelCommand("REAR", 0, "STOP"),
            }

    def _stop_all(self) -> Dict[str, WheelCommand]:
        """Stop all wheels"""
        return {
            "FL": WheelCommand("FL", 0, "STOP"),
            "FR": WheelCommand("FR", 0, "STOP"),
            "REAR": WheelCommand("REAR", 0, "STOP"),
        }

    def _log_command(self, commands: Dict[str, WheelCommand]):
        """Log command for debugging"""
        if config.DEBUG_COMMANDS:
            cmd_str = " | ".join([
                f"{name}: {cmd.speed}({cmd.direction[0]})"
                for name, cmd in commands.items()
            ])
            logger.debug(f"Wheels: {cmd_str}")

    def get_last_command(self) -> Dict[str, WheelCommand]:
        """Get the last command that was processed"""
        return self.last_command or self._stop_all()

    def get_command_history(self, limit: int = 10) -> list:
        """Get recent command history"""
        return [
            {name: cmd.to_dict() for name, cmd in cmds.items()}
            for cmds in self.command_history[-limit:]
        ]
