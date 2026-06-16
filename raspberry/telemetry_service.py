"""
Serviço de telemetria - recolhe e transmite dados do robô
Integra monitorização de sistema, GPS, bateria e estado de controlo
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass, asdict
from datetime import datetime

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

        # Controlo de atualização de localização
        self.last_sent_lat: Optional[float] = None
        self.last_sent_lon: Optional[float] = None

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
            logger.info("✓ Serviço de telemetria iniciado")

    async def stop(self):
        """Para a recolha de telemetria."""
        if self.collection_task:
            self.collection_task.cancel()
            try:
                await self.collection_task
            except asyncio.CancelledError:
                pass
            self.collection_task = None
            logger.info("✓ Serviço de telemetria parado")

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
        """Loop principal de telemetria com três ritmos: local, live e histórico."""
        last_firebase_live = datetime.now()
        last_history_save = datetime.now()

        while True:
            try:
                telemetry = await self._collect_telemetry()
                self.latest_telemetry = telemetry

                # Histórico local em RAM (mantém os últimos 1000 registos)
                self.telemetry_history.append(telemetry)
                if len(self.telemetry_history) > 1000:
                    self.telemetry_history.pop(0)

                now = datetime.now()

                # 1. Verificar se é hora de atualizar o STATUS ATUAL (Live)
                elapsed_live = (now - last_firebase_live).total_seconds()

                # 2. Verificar se é hora de gravar no HISTÓRICO ( Snapshot)
                elapsed_history = (now - last_history_save).total_seconds()

                save_history_flag = False
                if elapsed_history >= config.TELEMETRY_HISTORY_INTERVAL:
                    save_history_flag = True
                    last_history_save = now
                    logger.info("🕒 Intervalo de histórico atingido (10 min).")
                
                # Se atingiu o intervalo de 10 segundos, envia para o Firebase
                if elapsed_live >= self.firebase_save_interval:
                    # Lógica para evitar spam de localização quando parado
                    should_update_location = True
                    if (self.last_sent_lat is not None and 
                        self.last_sent_lon is not None and 
                        telemetry.gps_is_valid):
                        
                        lat_diff = abs(self.last_sent_lat - telemetry.gps_latitude)
                        lon_diff = abs(self.last_sent_lon - telemetry.gps_longitude)
                        
                        if lat_diff < config.LOCATION_UPDATE_THRESHOLD and lon_diff < config.LOCATION_UPDATE_THRESHOLD:
                            should_update_location = False
                    
                    # Se não houve movimento significativo, envia as coordenadas antigas para não mover no mapa
                    if not should_update_location:
                        telemetry.gps_latitude = self.last_sent_lat
                        telemetry.gps_longitude = self.last_sent_lon

                    if self.on_telemetry_update:
                        self.on_telemetry_update(telemetry, save_history=save_history_flag)

                    # Atualiza a última posição enviada apenas se foi uma atualização real e válida
                    if telemetry.gps_is_valid and should_update_location:
                        self.last_sent_lat = telemetry.gps_latitude
                        self.last_sent_lon = telemetry.gps_longitude

                    last_firebase_live = now

                if config.DEBUG_MODE:
                    logger.debug(f"Telemetria: V={telemetry.battery_voltage} | GuardarHistorico={save_history_flag}")

                # O loop "acorda" no ritmo do Broadcast Interval (2s)
                await asyncio.sleep(self.collection_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erro no loop de recolha de telemetria: {e}")
                await asyncio.sleep(2)

    async def _collect_telemetry(self) -> RobotTelemetry:
        """Faz o polling de todos os sensores e hardware."""
        try:
            # Dados do Sistema (Pi)
            sys_metrics = await self.system_monitor.get_metrics()

            # Dados do Arduino (Seguro contra falhas de conexão)
            gps = self.serial_handler.get_latest_gps()
            battery = self.serial_handler.get_latest_battery()
            sensors = self.serial_handler.get_latest_sensors()

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
            robot_moving=False, robot_rotation_direction="NONE"
        )

    async def health_check(self) -> Dict[str, Any]:
        """Diagnóstico do serviço."""
        return {
            "status": "online" if self.collection_task else "offline",
            "history_count": len(self.telemetry_history),
            "arduino_connected": self.serial_handler.is_connected,
            "active_controller": self.active_controller or "none"
        }
