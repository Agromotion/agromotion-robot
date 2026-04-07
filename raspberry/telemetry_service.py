"""
Telemetry service - collects and broadcasts robot data
Integrates system monitoring, GPS, battery, and control status
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict
from datetime import datetime
import json

import config
from system_monitor import SystemMonitor, SystemMetrics
from serial_handler import SerialHandler, GPSData, BatteryData

logger = logging.getLogger(__name__)


@dataclass
class RobotTelemetry:
    """Complete telemetry data for the robot"""
    
    timestamp: str
    
    # System metrics
    system_cpu: float
    system_ram: float
    system_temperature: float
    
    # Battery
    battery_voltage: float
    battery_percentage: float
    battery_current: float
    battery_is_charging: bool
    battery_temperature: float
    
    # GPS
    gps_latitude: float
    gps_longitude: float
    gps_altitude: float
    gps_is_valid: bool
    
    # Robot status
    robot_moving: bool
    robot_rotation_direction: str  # "CW", "CCW", "NONE"
    active_controller_email: Optional[str]
    
    # Video clients
    video_client_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Firebase/JSON"""
        return asdict(self)


class TelemetryService:
    """
    Centralized telemetry collection and broadcasting
    
    Collects data from:
    - System Monitor (CPU, RAM, Temp)
    - Serial Handler (GPS, Battery)
    - Command Handler (Robot state)
    - Control Manager (Who's controlling)
    
    Broadcasts to:
    - Firebase (periodic save)
    - WebSocket clients (real-time)
    """

    def __init__(
        self,
        system_monitor: SystemMonitor,
        serial_handler: SerialHandler,
    ):
        self.system_monitor = system_monitor
        self.serial_handler = serial_handler
        
        # Current state
        self.latest_telemetry: Optional[RobotTelemetry] = None
        self.robot_moving = False
        self.robot_rotation = "NONE"
        self.active_controller = None
        self.video_client_count = 0
        
        # Callbacks for subscribers
        self.on_telemetry_update: Optional[Callable] = None
        
        # Collection settings
        self.collection_interval = config.TELEMETRY_BROADCAST_INTERVAL
        self.firebase_save_interval = config.TELEMETRY_FIREBASE_INTERVAL
        
        # Collection task
        self.collection_task = None
        
        # History for Firebase
        self.telemetry_history = []

    async def start(self):
        """Start telemetry collection loop"""
        logger.info("✓ Telemetry service started")
        self.collection_task = asyncio.create_task(self._collection_loop())

    async def stop(self):
        """Stop telemetry collection"""
        if self.collection_task:
            self.collection_task.cancel()
            try:
                await self.collection_task
            except asyncio.CancelledError:
                pass
        logger.info("✓ Telemetry service stopped")

    async def update_robot_state(
        self,
        moving: bool = None,
        rotation: str = None,
        controller_email: str = None
    ):
        """Update robot state that affects telemetry"""
        if moving is not None:
            self.robot_moving = moving
        if rotation is not None:
            self.robot_rotation = rotation
        if controller_email is not None:
            self.active_controller = controller_email

    def set_video_client_count(self, count: int):
        """Update number of video clients"""
        self.video_client_count = count
        if count >= config.MAX_VIDEO_CLIENTS:
            logger.warning(f"⚠️ Max video clients ({config.MAX_VIDEO_CLIENTS}) reached")

    async def get_current_telemetry(self) -> RobotTelemetry:
        """Get the latest telemetry"""
        if self.latest_telemetry is None:
            return await self._collect_telemetry()
        return self.latest_telemetry

    async def _collection_loop(self):
        """Main loop that collects telemetry periodically"""
        last_firebase_save = datetime.now()
        
        while True:
            try:
                # Collect telemetry
                telemetry = await self._collect_telemetry()
                self.latest_telemetry = telemetry
                
                # Store in history
                self.telemetry_history.append(telemetry)
                if len(self.telemetry_history) > 1000:  # Keep last 1000
                    self.telemetry_history.pop(0)
                
                # Call subscriber callback
                if self.on_telemetry_update:
                    self.on_telemetry_update(telemetry)
                
                # Save to Firebase periodically
                elapsed = (datetime.now() - last_firebase_save).total_seconds()
                if elapsed >= self.firebase_save_interval:
                    await self._save_to_firebase(telemetry)
                    last_firebase_save = datetime.now()
                
                await asyncio.sleep(self.collection_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in telemetry collection: {e}")
                await asyncio.sleep(1)

    async def _collect_telemetry(self) -> RobotTelemetry:
        """Collect all telemetry data"""
        
        try:
            # System metrics
            sys_metrics = await self.system_monitor.get_metrics()
            
            # GPS and Battery from Arduino
            gps = self.serial_handler.get_latest_gps()
            battery = self.serial_handler.get_latest_battery()
            
            telemetry = RobotTelemetry(
                timestamp=datetime.now().isoformat(),
                
                # System
                system_cpu=sys_metrics.cpu_percent,
                system_ram=sys_metrics.ram_percent,
                system_temperature=sys_metrics.temperature_celsius,
                
                # Battery
                battery_voltage=battery.voltage,
                battery_percentage=battery.percentage,
                battery_current=battery.current,
                battery_is_charging=battery.is_charging,
                battery_temperature=battery.temperature,
                
                # GPS
                gps_latitude=gps.latitude,
                gps_longitude=gps.longitude,
                gps_altitude=gps.altitude,
                gps_is_valid=gps.is_valid,
                
                # Robot status
                robot_moving=self.robot_moving,
                robot_rotation_direction=self.robot_rotation,
                active_controller_email=self.active_controller,
                
                # Video
                video_client_count=self.video_client_count,
            )
            
            return telemetry
            
        except Exception as e:
            logger.error(f"Error collecting telemetry: {e}")
            # Return minimal telemetry on error
            return RobotTelemetry(
                timestamp=datetime.now().isoformat(),
                system_cpu=0, system_ram=0, system_temperature=0,
                battery_voltage=0, battery_percentage=0, battery_current=0,
                battery_is_charging=False, battery_temperature=0,
                gps_latitude=0, gps_longitude=0, gps_altitude=0,gps_is_valid=False,
                robot_moving=False, robot_rotation_direction="NONE",
                active_controller_email=None,
                video_client_count=0
            )

    async def _save_to_firebase(self, telemetry: RobotTelemetry):
        """Save telemetry to Firebase (called periodically)"""
        try:
            if config.DEBUG_MODE:
                logger.debug(f"Saving telemetry to Firebase: {telemetry.timestamp}")
        except Exception as e:
            logger.error(f"Error saving telemetry to Firebase: {e}")

    def get_telemetry_summary(self) -> Dict[str, Any]:
        """Get a summary of current telemetry"""
        if not self.latest_telemetry:
            return {}
        
        t = self.latest_telemetry
        
        return {
            "timestamp": t.timestamp,
            "system": {
                "cpu": f"{t.system_cpu:.1f}%",
                "ram": f"{t.system_ram:.1f}%",
                "temperature": f"{t.system_temperature:.1f}°C"
            },
            "battery": {
                "voltage": f"{t.battery_voltage:.1f}V",
                "percentage": f"{t.battery_percentage:.1f}%",
                "current": f"{t.battery_current:.2f}A",
                "charging": t.battery_is_charging
            },
            "gps": {
                "latitude": f"{t.gps_latitude:.6f}",
                "longitude": f"{t.gps_longitude:.6f}",
                "valid": t.gps_is_valid
            },
            "robot": {
                "moving": t.robot_moving,
                "rotation": t.robot_rotation_direction,
                "controller": t.active_controller or "idle",
                "video_clients": f"{t.video_client_count}/{config.MAX_VIDEO_CLIENTS}"
            }
        }

    def get_json_summary(self) -> str:
        """Get JSON representation of summary"""
        return json.dumps(self.get_telemetry_summary(), indent=2)

    async def get_history(self, limit: int = 100) -> list:
        """Get recent telemetry history"""
        return [
            t.to_dict()
            for t in self.telemetry_history[-limit:]
        ]

    async def health_check(self) -> Dict[str, Any]:
        """Check telemetry service health"""
        return {
            "is_running": self.collection_task is not None,
            "latest_timestamp": self.latest_telemetry.timestamp if self.latest_telemetry else None,
            "history_size": len(self.telemetry_history),
            "video_clients": self.video_client_count,
            "controller": self.active_controller
        }
