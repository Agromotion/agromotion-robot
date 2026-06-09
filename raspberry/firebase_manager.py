import logging
import asyncio
import json
from typing import Dict, Any, Optional, Callable
import firebase_admin
from firebase_admin import credentials, firestore

import config
from notification_service import NotificationService
from control_access_manager import ControlAccessManager
from schedule_manager import ScheduleManager
from webrtc_manager import WebRTCManager

logger = logging.getLogger(__name__)

class FirebaseManager:
    def __init__(self, robot_instance=None):
        self.initialized = False
        self.robot_id = config.ROBOT_ID
        self.db: Optional[firestore.Client] = None
        self.doc_ref = None
        self.robot = robot_instance
        self.notification_service = None
        
        self.on_auto_mode_change: Optional[Callable[[bool, bool], None]] = None
        self._last_auto_mode_state: Optional[bool] = None

        # Gestão de Acesso e Fila
        self.access_manager = ControlAccessManager()
        self.current_controller = None
        
        # Sub-Managers de domínio
        self.schedule_manager: Optional[ScheduleManager] = None
        self.webrtc_manager: Optional[WebRTCManager] = None

        self.loop = asyncio.get_event_loop()
        self.on_control_change: Optional[Callable] = None
        self.connected = False
        self._snapshot_listener = None
        self._timeout_task = None

    def initialize(self) -> bool:
        """Inicializa a ligação ao Firebase e limpa estados residuais."""
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(config.FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)

            self.db = firestore.client()
            self.doc_ref = self.db.collection('robots').document(self.robot_id)

            logger.info("Limpando sessões WebRTC e reiniciando fila de controlo...")
            self.doc_ref.update({
                'webrtc_session': None,
                'app_candidates': [],
                'robot_candidates': [],
                'status.online': True,
                'status.video_client_count': 0,
                'status.last_boot': firestore.SERVER_TIMESTAMP,
                'control.active_controller_email': None,
                'control.viewer_queue': [],
                'control.last_access': firestore.SERVER_TIMESTAMP
            })

            self.notification_service = NotificationService(self.db, self.robot_id)

            self.schedule_manager = ScheduleManager(self.doc_ref, self.notification_service, self.loop)
            self.webrtc_manager = WebRTCManager(self.doc_ref, self.loop, self.access_manager, self.robot)
            self.webrtc_manager.on_disconnect = self._promote_next_controller

            self._timeout_task = asyncio.run_coroutine_threadsafe(self._control_timeout_loop(), self.loop)
            self.schedule_manager.start()

            self.connected = True
            self.initialized = True
            logger.info("✓ Firebase Manager inicializado com sucesso.")
            return True
        except Exception as e:
            logger.error(f"✗ Falha crítica no Firebase: {e}")
            return False

    def _start_firestore_listener(self):
        """Monitoriza o Firestore para Handshake WebRTC e Fila de Controlo."""
        def on_snapshot(doc_snapshot, changes, read_time):
            for doc in doc_snapshot:
                data = doc.to_dict()
                if not data:
                    continue

                control_data = data.get('control', {})
                app_email = control_data.get('last_handshake_email')
                session = data.get('webrtc_session')
                status_data = data.get('status', {})

                auto_mode = status_data.get('autoMode')
                if isinstance(auto_mode, bool):
                    self._handle_auto_mode_change(auto_mode)

                # 1. TRATAMENTO DE HANDSHAKE (OFFER)
                if session and session.get('offer') and not session.get('answer'):
                    if self.webrtc_manager.pc and self.webrtc_manager.pc.iceConnectionState in ["checking", "connected", "completed"]:
                        logger.warning("🚫 Ignorando nova offer - handshake em progresso")
                        continue

                    if not self.webrtc_manager.handling_offer:
                        if app_email:
                            logger.info(f"⚙️ Solicitando controlo e bloqueando timeout para: {app_email}")
                            self.access_manager.request_control(app_email)
                            self.access_manager.update_activity(app_email)  # Evita timeout imediato
                            self.current_controller = self.access_manager.current_controller
                            self._sync_control_state()
                        
                        logger.info(f"📡 Oferta WebRTC de {app_email}. Iniciando conexão...")
                        asyncio.run_coroutine_threadsafe(
                            self._handle_webrtc_offer(session['offer']),
                            self.loop
                        )
                        continue  # Alterado de return para continue (Corrige o crash de inicialização)

                # 2. PROTEÇÃO DE SESSÃO ATIVA 
                if self.pc and self.pc.iceConnectionState in ["checking", "completed", "connected"]:
                    if not app_email or app_email == 'unknown':
                        logger.debug("Snapshot de limpeza ignorado para proteger Handshake ativo.")
                        continue

                # 3. CANDIDATOS ICE
                app_candidates = data.get('app_candidates', [])
                if app_candidates and self.pc:
                    for cand_data in app_candidates:
                        cand_str = cand_data.get('candidate')
                        if cand_str and cand_str not in self._processed_app_candidates:
                            self._processed_app_candidates.add(cand_str)
                            if self._remote_description_set:
                                asyncio.run_coroutine_threadsafe(
                                    self._add_ice_candidate(cand_data),
                                    self.loop
                                )
                            else:
                                self._pending_candidates.append(cand_data)

        self._snapshot_listener = self.doc_ref.on_snapshot(on_snapshot)

    async def _control_timeout_loop(self):
            """Verifica periodicamente se o controlador atual expirou."""
            while True:
                await asyncio.sleep(2)
                if self.current_controller:
                    # SÓ verifica inatividade se a ligação NÃO estiver em negociação/handshake
                    if self.pc and self.pc.iceConnectionState in ["checking"]:
                        self.access_manager.update_activity(self.current_controller)
                        continue

                    # Se a ligação falhou ou está ligada mas o joystick parou de enviar dados há > 7s
                    if self.access_manager.is_inactive(timeout_seconds=20):
                        logger.warning(f"Detetada perda de sinal de {self.current_controller}. A libertar...")
                        if self.pc:
                            try:
                                self.pc.on("iceconnectionstatechange", None)
                                self.pc.on("icecandidate", None)
                                self.pc.on("datachannel", None)
                                await self.pc.close()
                            except Exception:
                                pass
                            self.pc = None
                        await self._promote_next_controller()

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
                sched_time = sched.get('time')
                sched_days = sched.get('days', '')
                sched_id = sched.get('id')

                if sched_time == current_time:
                    if self._is_day_match(sched_days, weekday):
                        # Evita disparar repetidamente no mesmo minuto ou no mesmo dia
                        if self._triggered_schedules.get(sched_id) != current_date:
                            logger.info(f"⏰ Agendamento disparado: {sched_time} ({sched_days})")
                            self._triggered_schedules[sched_id] = current_date
                            
                            # Ativa o modo automático no Firestore (propaga para todos os clientes e Hardware)
                            try:
                                self.doc_ref.update({'status.autoMode': True})
                                if self.notification_service:
                                    self.notification_service.broadcast_alert(
                                        "Agendamento Ativado",
                                        f"O modo automático iniciou conforme o agendamento das {sched_time}.",
                                        "info"
                                    )
                            except Exception as e:
                                logger.error(f"Erro ao ativar autoMode por agendamento: {e}")

    def _is_day_match(self, days_str: str, weekday: int) -> bool:
        if not days_str:
            return False
        
        def clean_str(s):
            # Remove acentos e converte para minúsculas
            return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower()

        clean_days = clean_str(days_str)
        if "segunda a domingo" in clean_days or "todos os dias" in clean_days:
            return True
        if "dias uteis" in clean_days:
            return weekday in [0, 1, 2, 3, 4]
        if "fins de semana" in clean_days or "fim de semana" in clean_days:
            return weekday in [5, 6]

        pt_days = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
        return pt_days[weekday] in clean_days

    async def _promote_next_controller(self):
        """Liberta o controlo atual e passa para o próximo na fila."""
        if self.current_controller:
            self.access_manager.release_control(self.current_controller)
        status = self.access_manager.get_control_status()
        next_user = status['current_controller']
        self.current_controller = next_user
        self._sync_control_state()
        if self.on_control_change:
            self.on_control_change(next_user, next_user is not None)

    def _sync_control_state(self):
        """Sincroniza o estado do AccessManager com o Firestore."""
        self.doc_ref.update({
            'control.active_controller_email': self.access_manager.current_controller,
            'control.viewer_queue': self.access_manager.control_queue,
            'status.video_client_count': len(self.access_manager.control_queue) + (1 if self.access_manager.current_controller else 0)
        })

    def _handle_auto_mode_change(self, enabled: bool, force: bool = False):
        if not force and enabled == self._last_auto_mode_state:
            return

        self._last_auto_mode_state = enabled
        logger.info(f"Modo automático atualizado: {'ON' if enabled else 'OFF'}")

        if self.on_auto_mode_change:
            try:
                self.loop.call_soon_threadsafe(self.on_auto_mode_change, enabled, force)
            except RuntimeError:
                self.on_auto_mode_change(enabled, force)

    def get_current_auto_mode(self) -> Optional[bool]:
        try:
            snapshot = self.doc_ref.get()
            if not snapshot.exists:
                return None

            data = snapshot.to_dict() or {}
            status_data = data.get('status', {})
            auto_mode = status_data.get('autoMode')
            return auto_mode if isinstance(auto_mode, bool) else None
        except Exception as e:
            logger.debug(f"Não foi possível ler autoMode atual: {e}")
            return None

    async def save_telemetry(self, data: Dict[str, Any], save_history: bool = False):
        if not self.initialized:
            return
        try:
            # Substitui a timestamp local (do Pi) pela timestamp oficial da Google
            data['timestamp'] = firestore.SERVER_TIMESTAMP

            self.doc_ref.set(
                {'telemetry': data},
                merge=True
            )

            if save_history:
                logger.info("Tentando gravar histórico...")
                self.doc_ref.collection('telemetry_history').add({
                    **data
                })
                logger.info("✅ Histórico gravado com sucesso!")

        except Exception as e:
            logger.error(f"Erro ao gravar telemetria: {e}")

    def start_listening(self):
        """Inicia a escuta de comandos. Só deve ser chamado quando o vídeo estiver OK."""
        if not self._snapshot_listener:
            logger.info("👂 Robô agora está a ouvir pedidos de conexão (Signaling ativo).")
            self._start_firestore_listener()
            # Atualiza o Firestore para dizer às Apps que já podem enviar Offers
            self.doc_ref.update({'status.video_ready': True})

    async def disconnect(self):
        """Fecha todas as conexões de forma limpa."""
        logger.info("Encerrando Firebase Manager...")
        if self._snapshot_listener:
            self._snapshot_listener.unsubscribe()
        if self._timeout_task:
            self._timeout_task.cancel()
            
        if self.schedule_manager:
            self.schedule_manager.stop()
        if self.webrtc_manager:
            await self.webrtc_manager.close()
            
        if self.initialized:
            self.doc_ref.update({
                'status.online': False,
                'status.video_ready': False, # IMPORTANTE
                'webrtc_session': None
            })

    async def acquire_control_lock(self, user_email: str):
        self.doc_ref.update({
            'control.current_controller': user_email,
            'control.lock_time': firestore.SERVER_TIMESTAMP
        })

    async def release_control_lock(self):
        self.doc_ref.update({
            'control.current_controller': None,
            'control.lock_time': None
        })

    async def health_check(self) -> Dict[str, Any]:
        try:
            doc = self.doc_ref.get()
            return {
                "connected": doc.exists,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "webrtc_active": self.webrtc_manager is not None and self.webrtc_manager.pc is not None and self.webrtc_manager.pc.iceConnectionState == "completed"
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}
