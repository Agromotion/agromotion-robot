import psutil
import logging
import os
from typing import Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import config

logger = logging.getLogger(__name__)

@dataclass
class SystemMetrics:
    timestamp: str
    cpu_percent: float
    cpu_count: int
    cpu_freq_mhz: float
    ram_percent: float
    ram_available_mb: float
    temperature_celsius: float
    disk_percent: float
    uptime_seconds: float
    load_average: tuple

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['load_average'] = list(self.load_average)
        return data

class SystemMonitor:
    def __init__(self):
        self.latest_metrics = None
        self.notification_service = None # Set by firmware.py

    async def get_metrics(self) -> SystemMetrics:
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory()
            temp = self._get_temperature()
            disk = psutil.disk_usage('/')
            
            # Stress Notifications
            if self.notification_service:
                if temp > 80:
                    self.notification_service.broadcast_alert(
                        "Alerta Térmico", 
                        f"A temperatura da CPU atingiu {temp}°C! Verifique a ventilação.", 
                        "warning"
                    )
                if ram.percent > 90:
                    self.notification_service.broadcast_alert(
                        "Memória Crítica", 
                        f"Uso de RAM crítico ({ram.percent}%). O sistema pode sofrer lentidão.", 
                        "warning"
                    )

            metrics = SystemMetrics(
                timestamp=datetime.now().isoformat(),
                cpu_percent=cpu,
                cpu_count=psutil.cpu_count(logical=False),
                cpu_freq_mhz=psutil.cpu_freq().current if psutil.cpu_freq() else 0.0,
                ram_percent=ram.percent,
                ram_available_mb=ram.available / (1024 * 1024),
                temperature_celsius=temp,
                disk_percent=disk.percent,
                uptime_seconds=datetime.now().timestamp() - psutil.boot_time(),
                load_average=os.getloadavg()
            )
            self.latest_metrics = metrics
            return metrics
        except Exception as e:
            logger.error(f"Metrics collection failed: {e}")
            return None

    def _get_temperature(self) -> float:
        try:
            with open(config.TEMPERATURE_SENSOR_PATH, 'r') as f:
                return int(f.read().strip()) / 1000.0
        except:
            temps = psutil.sensors_temperatures()
            return temps['cpu-thermal'][0].current if 'cpu-thermal' in temps else 0.0

    def get_health_status(self) -> Dict[str, Any]:
        m = self.latest_metrics
        if not m: return {"status": "ERROR"}
        
        status = "OK"
        if m.cpu_percent > 90 or m.ram_percent > 90 or m.temperature_celsius > 80:
            status = "WARNING"
            
        return {
            "status": status, 
            "cpu": f"{m.cpu_percent}%", 
            "temp": f"{m.temperature_celsius}°C",
            "ram": f"{m.ram_percent}%"
        }