"""
Telemetry service - collects and broadcasts robot data
Integrates system monitoring, GPS, battery, and control status
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass, asdict
from datetime import datetime
import json

import config
from system_monitor import SystemMonitor
from serial_handler import SerialHandler

logger = logging.getLogger(__name__)

@dataclass
class RobotTelemetry:
    """Dados telemétricos do robô"""
    timestamp: str
    
    # Sistema (dados do próprio Raspberry Pi)
    system_cpu: float
    system_ram: float
    system_temperature: float
    
    # Bateria
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
    
    # Status do robo
    robot_moving: bool
    robot_rotation_direction: str  # "CW", "CCW", "NONE"
    active_controller_email: Optional[str]
    
    # Clientes de video
    video_client_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TelemetryService:
    def __init__(
        self,
        system_monitor: SystemMonitor,
        serial_handler: SerialHandler,
    ):
        self.system_monitor = system_monitor
        self.serial_handler = serial_handler
        
        # Estado atual
        self.latest_telemetry: Optional[RobotTelemetry] = None
        self.robot_moving = False
        self.robot_rotation = "NONE"
        self.active_controller = None
        self.video_client_count = 0
        
        # Callback para o firmware.py enviar para o Firebase
        self.on_telemetry_update: Optional[Callable[[RobotTelemetry], None]] = None
        
        self.collection_interval = config.TELEMETRY_BROADCAST_INTERVAL
        self.firebase_save_interval = config.TELEMETRY_FIREBASE_INTERVAL
        
        self.collection_task: Optional[asyncio.Task] = None
        self.telemetry_history: List[RobotTelemetry] = []

    async def start(self):
        """Inicia o loop de recolha de telemetria."""
        if self.collection_task is None:
            self.collection_task = asyncio.create_task(self._collection_loop())
            logger.info("✓ Telemetry service started")

    async def stop(self):
        """Para a recolha de telemetria."""
        if self.collection_task:
            self.collection_task.cancel()
            try:
                await self.collection_task
            except asyncio.CancelledError:
                pass
            self.collection_task = None
            logger.info("✓ Telemetry service stopped")

    async def update_robot_state(
        self,
        moving: bool = None,
        rotation: str = None,
        controller_email: str = None
    ):
        """Atualiza os estados internos que vêm do firmware/comandos."""
        if moving is not None:
            self.robot_moving = moving
        if rotation is not None:
            self.robot_rotation = rotation
        if controller_email is not None:
            self.active_controller = controller_email

    async def _collection_loop(self):
        """Loop principal de telemetria."""
        last_firebase_save = datetime.now()
        
        while True:
            try:
                # 1. Recolher dados atuais
                telemetry = await self._collect_telemetry()
                self.latest_telemetry = telemetry
                
                # 2. Gerir histórico local (limite de 1000 entradas)
                self.telemetry_history.append(telemetry)
                if len(self.telemetry_history) > 1000:
                    self.telemetry_history.pop(0)
                
                # 3. Notificar o firmware para enviar para o Firebase/Websocket
                if self.on_telemetry_update:
                    self.on_telemetry_update(telemetry)
                
                # 4. Log de debug se ativo
                if config.DEBUG_MODE:
                    logger.debug(f"Telemetry updated: Bat={telemetry.battery_voltage}V, GPS={telemetry.gps_is_valid}")
                
                await asyncio.sleep(self.collection_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in telemetry collection loop: {e}")
                await asyncio.sleep(2)

    async def _collect_telemetry(self) -> RobotTelemetry:
        """Faz o polling de todos os sensores e hardware."""
        try:
            # Dados do Sistema (Pi)
            sys_metrics = await self.system_monitor.get_metrics()
            
            # Dados do Arduino (Seguro contra falhas de conexão)
            gps = self.serial_handler.get_latest_gps()
            battery = self.serial_handler.get_latest_battery()
            
            return RobotTelemetry(
                timestamp=datetime.now().isoformat(),
                
                # System
                system_cpu=sys_metrics.cpu_percent,
                system_ram=sys_metrics.ram_percent,
                system_temperature=sys_metrics.temperature_celsius,
                
                # Battery
                battery_voltage=round(battery.voltage, 2),
                battery_percentage=round(battery.percentage, 1),
                battery_current=round(battery.current, 2),
                battery_is_charging=battery.is_charging,
                battery_temperature=round(battery.temperature, 1),
                
                # GPS
                gps_latitude=gps.latitude,
                gps_longitude=gps.longitude,
                gps_altitude=gps.altitude,
                gps_is_valid=gps.is_valid,
                
                # Status
                robot_moving=self.robot_moving,
                robot_rotation_direction=self.robot_rotation,
                active_controller_email=self.active_controller,
                
                # Video
                video_client_count=self.video_client_count,
            )
            
        except Exception as e:
            logger.error(f"Falha ao recolher telemetria: {e}")
            # Fallback em caso de erro para não quebrar a App
            return self._get_empty_telemetry()

    def _get_empty_telemetry(self) -> RobotTelemetry:
        """Retorna um objeto vazio/seguro em caso de erro de sensores."""
        return RobotTelemetry(
            timestamp=datetime.now().isoformat(),
            system_cpu=0, system_ram=0, system_temperature=0,
            battery_voltage=0, battery_percentage=0, battery_current=0,
            battery_is_charging=False, battery_temperature=0,
            gps_latitude=0, gps_longitude=0, gps_altitude=0, gps_is_valid=False,
            robot_moving=False, robot_rotation_direction="NONE",
            active_controller_email=self.active_controller,
            video_client_count=self.video_client_count
        )

    def get_telemetry_summary(self) -> Dict[str, Any]:
        """Versão formatada para logs humanos ou display simples."""
        if not self.latest_telemetry:
            return {"status": "No data"}
        
        t = self.latest_telemetry
        return {
            "time": t.timestamp.split("T")[1][:8],
            "bat": f"{t.battery_percentage}% ({t.battery_voltage}V)",
            "cpu": f"{t.system_cpu}%",
            "gps": "FIX" if t.gps_is_valid else "NO_FIX",
            "moving": t.robot_moving,
            "clients": t.video_client_count
        }

    async def health_check(self) -> Dict[str, Any]:
        """Diagnóstico do serviço."""
        return {
            "status": "online" if self.collection_task else "offline",
            "history_count": len(self.telemetry_history),
            "arduino_connected": self.serial_handler.is_connected,
            "active_controller": self.active_controller or "none"
        }