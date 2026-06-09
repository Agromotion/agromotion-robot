import asyncio
import json
import serial
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class GPSData:
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    satellites: int = 0
    hdop: float = 0.0
    timestamp: str = ""
    is_valid: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class BatteryData:
    voltage: float = 0.0
    percentage: float = 0.0
    current: float = 0.0
    temperature: float = 0.0
    is_charging: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class SensorData:
    obs_left: bool = False
    obs_center: bool = False
    obs_right: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class SerialHandler:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_connection: Optional[serial.Serial] = None
        self.is_connected = False
        self.notification_service = None # Ligado via firmware.py
        self._write_lock = asyncio.Lock()
        
        self.latest_gps = GPSData()
        self.latest_battery = BatteryData()
        self.latest_sensors = SensorData()
        self._gps_was_valid = False
        
        self.on_gps_received = None
        self.on_battery_received = None
        self.on_error_received = None
        self.pending_commands = {}
        self.last_heartbeat = datetime.now()

    async def connect(self) -> bool:
        try:
            self.serial_connection = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            self.is_connected = True
            logger.info(f"✓ Ligado ao Arduino na porta {self.port}")
            asyncio.create_task(self._read_loop())
            return True
        except Exception as e:
            logger.error(f"✗ Falha ao ligar ao Arduino: {e}")
            return False

    async def disconnect(self):
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
            self.is_connected = False
            logger.info("✓ Desligado do Arduino")

    async def send_command(self, command: Dict[str, Any]) -> bool:
        return await self._send_command(command)

    async def send_move_command(self, left: int, right: int, duration_ms: int = 100) -> bool:
        if not self.is_connected: return False
        command = {
            "cmd": "MOVE",
            "wheels": {
                "L": max(-255, min(255, left)),
                "R": max(-255, min(255, right))
            },
            "duration": max(10, min(5000, duration_ms))
        }
        return await self.send_command(command)

    async def send_stop_command(self) -> bool:
        return await self.send_command({"cmd": "STOP"}) if self.is_connected else False

    async def send_ping(self) -> bool:
        return await self.send_command({"cmd": "PING"}) if self.is_connected else False

    async def send_auto_mode_command(self, enabled: bool) -> bool:
        if not self.is_connected:
            return False
        return await self.send_command({"cmd": "AUTO_MODE", "enabled": bool(enabled)})

    async def _send_command(self, command: Dict[str, Any]) -> bool:
        try:
            json_str = json.dumps(command) + "\n"
            if not self.serial_connection or not self.serial_connection.is_open:
                return False

            async with self._write_lock:
                self.serial_connection.write(json_str.encode())
            logger.info(f"→ Enviado: {json_str.strip()}")
            return True
        except Exception as e:
            logger.error(f"✗ Falha ao enviar comando: {e}")
            self.is_connected = False
            return False

    async def _read_loop(self):
        buffer = ""
        while self.is_connected:
            try:
                if self.serial_connection.in_waiting:
                    data = self.serial_connection.read(self.serial_connection.in_waiting)
                    buffer += data.decode('utf-8', errors='ignore')
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            await self._process_message(line.strip())
                await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"✗ Erro de leitura série: {e}")
                if self.notification_service:
                    self.notification_service.broadcast_alert(
                        "Hardware Offline", 
                        "A conexão serial com o Arduino Nano ESP32 foi perdida!", 
                        "error"
                    )
                self.is_connected = False
                break

    async def _process_message(self, message: str):
        if not message or not message.startswith('{'):
            return
        try:
            data = json.loads(message)
            msg_type = data.get("type", "").upper()

            if msg_type == "GPS":
                valid = data.get("is_valid", False)
                self.latest_gps = GPSData(
                    latitude=data.get("latitude", 0.0), 
                    longitude=data.get("longitude", 0.0),
                    altitude=data.get("altitude", 0.0), 
                    satellites=data.get("satellites", 0),
                    hdop=data.get("hdop", 0.0), 
                    timestamp=data.get("timestamp", ""), 
                    is_valid=valid
                )
                if self.on_gps_received: self.on_gps_received(self.latest_gps)
                
                if valid and not self._gps_was_valid:
                    self._gps_was_valid = True
                    if self.notification_service:
                        self.notification_service.broadcast_alert("Sinal GPS", "Localização confirmada com sucesso.", "success")
                elif not valid:
                    self._gps_was_valid = False

            elif msg_type == "BATTERY":
                pct = data.get("percentage", 0.0)
                self.latest_battery = BatteryData(
                    voltage=data.get("voltage", 0.0), 
                    percentage=pct,
                    current=data.get("current", 0.0), 
                    temperature=data.get("temperature", 0.0),
                    is_charging=data.get("is_charging", False)
                )
                if self.on_battery_received: self.on_battery_received(self.latest_battery)
                
                if pct < 15 and self.notification_service:
                    self.notification_service.broadcast_alert(
                        "Bateria Crítica", 
                        f"O robô atingiu {pct}%. Retorne à base para carregamento!", 
                        "error"
                    )

            elif msg_type == "SENSORS":
                self.latest_sensors = SensorData(
                    obs_left=data.get("left", False),
                    obs_center=data.get("center", False),
                    obs_right=data.get("right", False)
                )

            elif msg_type == "ACK":
                logger.debug(f"✓ ACK recebido para {data.get('cmd')}")

            elif msg_type == "ERROR":
                if self.on_error_received: self.on_error_received(data.get("error"))

        except json.JSONDecodeError:
            # Silencia o erro se não for um JSON completo (evita spam no log)
            logger.debug(f"Ignorada linha não-JSON: {message}")
        except Exception as e:
            logger.error(f"Erro ao processar mensagem: {e}")

    def get_latest_gps(self) -> GPSData: return self.latest_gps
    def get_latest_battery(self) -> BatteryData: return self.latest_battery
    def get_latest_sensors(self) -> SensorData: return self.latest_sensors

    async def health_check(self) -> Dict[str, Any]:
        """Verifica a saúde da ligação ao Arduino."""
        await self.send_ping()
        await asyncio.sleep(0.1)
        return {
            "connected": self.is_connected,
            "gps_valid": self.latest_gps.is_valid,
            "battery_voltage": f"{self.latest_battery.voltage}V",
            "last_gps_update": self.latest_gps.timestamp,
            "port": self.port
        }
