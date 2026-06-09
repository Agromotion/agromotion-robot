import logging
import asyncio
from datetime import datetime
import unicodedata

logger = logging.getLogger(__name__)

class ScheduleManager:
    def __init__(self, doc_ref, notification_service, loop):
        self.doc_ref = doc_ref
        self.notification_service = notification_service
        self.loop = loop
        
        self.schedules = []
        self._schedules_listener = None
        self._schedules_task = None
        self._triggered_schedules = {}
        self.initialized = False

    def start(self):
        self.initialized = True
        self._start_schedules_listener()
        self._schedules_task = asyncio.run_coroutine_threadsafe(self._schedule_checker_loop(), self.loop)

    def stop(self):
        self.initialized = False
        if self._schedules_listener:
            self._schedules_listener.unsubscribe()
        if self._schedules_task:
            self._schedules_task.cancel()

    def _start_schedules_listener(self):
        """Monitoriza a coleção schedules para carregar agendamentos ativos."""
        def on_snapshot(col_snapshot, changes, read_time):
            new_schedules = []
            for doc in col_snapshot:
                data = doc.to_dict()
                if data and data.get('active'):
                    data['id'] = doc.id
                    new_schedules.append(data)
            self.schedules = new_schedules
            logger.info(f"📅 Agendamentos atualizados: {len(self.schedules)} ativos.")

        schedules_ref = self.doc_ref.collection('schedules')
        self._schedules_listener = schedules_ref.on_snapshot(on_snapshot)

    async def _schedule_checker_loop(self):
        """Verifica periodicamente se algum agendamento deve ser acionado."""
        while True:
            await asyncio.sleep(20) # Verifica a cada 20 segundos
            if not self.initialized or not self.schedules:
                continue

            now = datetime.now()
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")
            weekday = now.weekday()

            for sched in self.schedules:
                if sched.get('time') == current_time and self._is_day_match(sched.get('days', ''), weekday):
                    sched_id = sched.get('id')
                    if self._triggered_schedules.get(sched_id) != current_date:
                        logger.info(f"⏰ Agendamento disparado: {current_time}")
                        self._triggered_schedules[sched_id] = current_date
                        try:
                            self.doc_ref.update({'status.autoMode': True})
                            if self.notification_service:
                                self.notification_service.broadcast_alert("Agendamento Ativado", f"O modo automático iniciou.", "info")
                        except Exception as e:
                            logger.error(f"Erro ao ativar autoMode por agendamento: {e}")

    def _is_day_match(self, days_str: str, weekday: int) -> bool:
        if not days_str: return False
        clean_days = ''.join(c for c in unicodedata.normalize('NFD', days_str) if unicodedata.category(c) != 'Mn').lower()
        if any(w in clean_days for w in ["segunda a domingo", "todos os dias"]): return True
        if "dias uteis" in clean_days: return weekday in [0, 1, 2, 3, 4]
        if any(w in clean_days for w in ["fins de semana", "fim de semana"]): return weekday in [5, 6]
        return ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"][weekday] in clean_days