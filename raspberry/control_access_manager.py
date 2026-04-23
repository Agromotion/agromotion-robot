"""
Control access manager - handles who can control the robot
Only one user can control at a time, others see who is controlling
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


class ControlAccessManager:
    """
    Gere o acesso exclusivo de controlo ao robô.
    
    - Apenas UM utilizador pode enviar comandos de cada vez (FIFO).
    - Outros utilizadores podem ver quem está a controlar e a sua posição na fila.
    - O controlo é libertado por desconexão, timeout de inatividade ou saída manual.
    """

    def __init__(self):
        self.current_controller: Optional[str] = None  # Email do utilizador ativo
        self.control_lock_time: Optional[datetime] = None
        self.control_timeout = timedelta(seconds=config.CONTROL_LOCK_TIMEOUT)
        self.last_activity_time: Optional[datetime] = None
        
        # Fila de utilizadores à espera (FIFO)
        self.control_queue: list = []
        
        # Estatísticas
        self.total_control_sessions = 0
        self.current_session_start = None

    def request_control(self, user_email: str) -> Dict[str, Any]:
        """
        Utilizador solicita controlo. Se livre, assume. Se ocupado, entra na fila.
        """
        # 1. Se estiver livre, concede imediatamente
        if self.current_controller is None:
            return self._grant_control(user_email)
        
        # 2. Se for o próprio utilizador ativo, atualiza atividade
        if self.current_controller == user_email:
            self._update_activity()
            return {
                "granted": True,
                "message": "Já tens o controlo ativo.",
                "current_controller": user_email,
                "time_until_available": None
            }
        
        # 3. Verifica se o controlo atual expirou por inatividade
        if self._is_control_expired():
            logger.info(f"Timeout de inatividade para {self.current_controller}. Rodando fila...")
            self._release_control()
            return self._grant_control(user_email)
        
        # 4. Está ocupado: adiciona à fila se ainda não estiver lá
        if user_email not in self.control_queue:
            self.control_queue.append(user_email)
            logger.info(f"Utilizador {user_email} adicionado à fila de espera.")
        
        return {
            "granted": False,
            "message": f"O robô está a ser controlado por {self.current_controller}",
            "current_controller": self.current_controller,
            "time_until_available": self._get_time_until_available(),
            "position_in_queue": self.control_queue.index(user_email) + 1
        }

    def release_control(self, user_email: str) -> Dict[str, Any]:
        """
        Liberta o controlo (desconexão ou saída manual). 
        Retorna o próximo utilizador na fila para promoção.
        """
        if self.current_controller != user_email:
            return {
                "released": False,
                "message": "Não tens o controlo ativo."
            }
        
        logger.info(f"✓ Controlo libertado por {user_email}")
        self._release_control()
        
        # Promove o próximo da fila
        next_controller = None
        if self.control_queue:
            next_controller = self.control_queue.pop(0)
            self._grant_control(next_controller)
            
            return {
                "released": True,
                "message": f"Controlo passado para {next_controller}",
                "next_controller": next_controller
            }
        
        return {
            "released": True,
            "message": "Controlo libertado. Robô em standby.",
            "next_controller": None
        }

    def update_activity(self, user_email: str) -> bool:
        """Atualiza timestamp de atividade para evitar timeout."""
        if self.current_controller != user_email:
            return False
        self._update_activity()
        return True

    def get_control_status(self) -> Dict[str, Any]:
        """Retorna o estado atual do controlo e verifica expiração."""
        if self.current_controller and self._is_control_expired():
            logger.warning(f"Controlo de {self.current_controller} expirou.")
            self._release_control()
            
            # Promove automaticamente o próximo se existir
            if self.control_queue:
                next_user = self.control_queue.pop(0)
                self._grant_control(next_user)

        return {
            "is_controlled": self.current_controller is not None,
            "current_controller": self.current_controller,
            "queue_length": len(self.control_queue),
            "time_until_available": self._get_time_until_available(),
            "total_sessions": self.total_control_sessions
        }

    def add_to_queue(self, user_email: str) -> int:
        if user_email not in self.control_queue and user_email != self.current_controller:
            self.control_queue.append(user_email)
        return self.control_queue.index(user_email) + 1 if user_email in self.control_queue else 0

    def remove_from_queue(self, user_email: str) -> bool:
        if user_email in self.control_queue:
            self.control_queue.remove(user_email)
            return True
        return False

    # ========================================================================
    # Métodos Privados
    # ========================================================================

    def _grant_control(self, user_email: str) -> Dict[str, Any]:
        self.current_controller = user_email
        self.control_lock_time = datetime.now()
        self.last_activity_time = datetime.now()
        self.current_session_start = datetime.now()
        self.total_control_sessions += 1
        
        if user_email in self.control_queue:
            self.control_queue.remove(user_email)
        
        logger.info(f"▶ CONTROLO CONCEDIDO A: {user_email}")
        return {
            "granted": True,
            "message": "Controlo concedido.",
            "current_controller": user_email,
            "time_until_available": None
        }

    def _release_control(self):
        self.current_controller = None
        self.control_lock_time = None
        self.last_activity_time = None
        self.current_session_start = None

    def _update_activity(self):
        self.last_activity_time = datetime.now()

    def _is_control_expired(self) -> bool:
        if not self.current_controller or not self.last_activity_time:
            return False
        return (datetime.now() - self.last_activity_time) > self.control_timeout

    def _get_time_until_available(self) -> Optional[int]:
        if self.current_controller is None: return 0
        if not self.last_activity_time: return None
        
        time_elapsed = datetime.now() - self.last_activity_time
        time_until = self.control_timeout - time_elapsed
        seconds = max(0, int(time_until.total_seconds()))
        return seconds if seconds > 0 else None

    def _get_session_duration(self) -> Optional[int]:
        if not self.current_session_start: return None
        return int((datetime.now() - self.current_session_start).total_seconds())

    def reset(self):
        """Reset total do estado (limpeza de boot)."""
        self._release_control()
        self.control_queue = []
        logger.info("Estado de controlo resetado.")