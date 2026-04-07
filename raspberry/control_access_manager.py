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
    Manages exclusive control access to the robot
    
    - Only ONE user can send commands at a time
    - Other users can see who is controlling
    - Control is released when user disconnects or after timeout
    - New user takes control when previous user releases it
    """

    def __init__(self):
        self.current_controller: Optional[str] = None  # email of controlling user
        self.control_lock_time: Optional[datetime] = None
        self.control_timeout = timedelta(seconds=config.CONTROL_LOCK_TIMEOUT)
        self.last_activity_time: Optional[datetime] = None
        self.activity_update_interval = config.CONTROL_LOCK_ACTIVITY_INTERVAL
        
        # Queue of users waiting to control
        self.control_queue: list = []
        
        # Statistics
        self.total_control_sessions = 0
        self.current_session_start = None

    def request_control(self, user_email: str) -> Dict[str, Any]:
        """
        User requests to take control
        
        Returns:
            {
                "granted": bool,
                "message": str,
                "current_controller": str or None,
                "time_until_available": int or None  # seconds
            }
        """
        
        # Check if control is available
        if self.current_controller is None:
            # No one controlling - grant control
            return self._grant_control(user_email)
        
        if self.current_controller == user_email:
            # Same user - just update activity
            self._update_activity()
            return {
                "granted": True,
                "message": "You have control",
                "current_controller": user_email,
                "time_until_available": None
            }
        
        # Different user is controlling
        if self._is_control_expired():
            # Control timed out - release and grant to new user
            logger.info(f"Control timeout for {self.current_controller}, releasing...")
            self._release_control()
            return self._grant_control(user_email)
        
        # Control is locked - add to queue if not already there
        if user_email not in self.control_queue:
            self.control_queue.append(user_email)
        
        time_until = self._get_time_until_available()
        
        return {
            "granted": False,
            "message": f"Robot is being controlled by {self.current_controller}",
            "current_controller": self.current_controller,
            "time_until_available": time_until,
            "position_in_queue": self.control_queue.index(user_email) + 1
        }

    def release_control(self, user_email: str) -> Dict[str, Any]:
        """
        User releases control (e.g., disconnects or leaves screen)
        
        Returns info about who gets control next
        """
        
        if self.current_controller != user_email:
            return {
                "released": False,
                "message": f"You don't have control (current: {self.current_controller})"
            }
        
        logger.info(f"✓ Control released by {user_email}")
        self._release_control()
        
        # Grant control to next user in queue
        next_controller = None
        if self.control_queue:
            next_controller = self.control_queue.pop(0)
            self._grant_control(next_controller)
            
            return {
                "released": True,
                "message": f"Control released. {next_controller} now has control",
                "next_controller": next_controller
            }
        
        return {
            "released": True,
            "message": "Control released. Robot idle",
            "next_controller": None
        }

    def update_activity(self, user_email: str) -> bool:
        """
        Update last activity time for current controller
        Call this periodically to prevent timeout
        """
        
        if self.current_controller != user_email:
            return False
        
        self._update_activity()
        return True

    def get_control_status(self) -> Dict[str, Any]:
        """Get current control status"""
        
        # Check for timeout
        if self.current_controller and self._is_control_expired():
            logger.warning("Control expired, releasing...")
            self._release_control()
        
        time_until = self._get_time_until_available()
        
        return {
            "is_controlled": self.current_controller is not None,
            "current_controller": self.current_controller,
            "is_available": self.current_controller is None,
            "time_until_available": time_until,
            "queue_position": self._get_queue_position(),
            "queue_length": len(self.control_queue),
            "session_duration": self._get_session_duration(),
            "total_sessions": self.total_control_sessions
        }

    def add_to_queue(self, user_email: str) -> int:
        """Add user to control queue, return position"""
        if user_email not in self.control_queue:
            self.control_queue.append(user_email)
        return self.control_queue.index(user_email) + 1

    def remove_from_queue(self, user_email: str) -> bool:
        """Remove user from queue"""
        if user_email in self.control_queue:
            self.control_queue.remove(user_email)
            return True
        return False

    # ========================================================================
    # Private methods
    # ========================================================================

    def _grant_control(self, user_email: str) -> Dict[str, Any]:
        """Internal: Grant control to user"""
        
        self.current_controller = user_email
        self.control_lock_time = datetime.now()
        self.last_activity_time = datetime.now()
        self.current_session_start = datetime.now()
        self.total_control_sessions += 1
        
        # Remove from queue if there
        if user_email in self.control_queue:
            self.control_queue.remove(user_email)
        
        logger.info(f"✓ Control granted to {user_email}")
        
        return {
            "granted": True,
            "message": f"Control granted to {user_email}",
            "current_controller": user_email,
            "time_until_available": None
        }

    def _release_control(self):
        """Internal: Release control"""
        self.current_controller = None
        self.control_lock_time = None
        self.last_activity_time = None
        self.current_session_start = None

    def _update_activity(self):
        """Internal: Update last activity timestamp"""
        self.last_activity_time = datetime.now()

    def _is_control_expired(self) -> bool:
        """Check if control has timed out"""
        if not self.current_controller or not self.last_activity_time:
            return False
        
        time_elapsed = datetime.now() - self.last_activity_time
        return time_elapsed > self.control_timeout

    def _get_time_until_available(self) -> Optional[int]:
        """Get seconds until control becomes available"""
        
        if self.current_controller is None:
            return 0
        
        if not self.last_activity_time:
            return None
        
        time_elapsed = datetime.now() - self.last_activity_time
        time_until = self.control_timeout - time_elapsed
        
        seconds = max(0, int(time_until.total_seconds()))
        return seconds if seconds > 0 else None

    def _get_queue_position(self) -> Optional[int]:
        """Get current user's position in queue"""
        # Would need to know current user - implemented at higher level
        return None

    def _get_session_duration(self) -> Optional[int]:
        """Get current session duration in seconds"""
        if not self.current_session_start:
            return None
        
        duration = datetime.now() - self.current_session_start
        return int(duration.total_seconds())

    def get_detailed_status(self) -> Dict[str, Any]:
        """Get detailed control status for logging/debug"""
        
        return {
            "current_controller": self.current_controller,
            "is_controlled": self.current_controller is not None,
            "control_duration_seconds": self._get_session_duration(),
            "time_until_timeout": self._get_time_until_available(),
            "control_timeout_seconds": config.CONTROL_LOCK_TIMEOUT,
            "queue": self.control_queue,
            "queue_length": len(self.control_queue),
            "total_sessions": self.total_control_sessions,
            "last_activity": self.last_activity_time.isoformat() if self.last_activity_time else None,
        }

    def reset(self):
        """Reset all control state (for testing/reset)"""
        self.current_controller = None
        self.control_lock_time = None
        self.last_activity_time = None
        self.current_session_start = None
        self.control_queue = []
        logger.info("Control state reset")
