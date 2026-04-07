import logging
import time
from firebase_admin import firestore, messaging

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, db, robot_id):
        self.db = db
        self.robot_id = robot_id
        # Topic name for FCM subscriptions (e.g., robot_agromotion_robot_01)
        self.topic_name = f"robot_{self.robot_id.replace('-', '_')}"
        self._cooldowns = {} # Stores {event_title: last_sent_time}

    def _get_authorized_emails(self):
        """Fetch emails from the authorized_emails collection."""
        try:
            docs = self.db.collection('authorized_emails').stream()
            return [doc.id for doc in docs]
        except Exception as e:
            logger.error(f"Erro ao buscar emails autorizados: {e}")
            return []

    def broadcast_alert(self, title, message, alert_type="info", cooldown_seconds=300):
        """
        Sends notifications with a cooldown to prevent spamming.
        alert_type: 'info', 'warning', 'error', 'success'
        """
        now = time.time()
        if title in self._cooldowns and (now - self._cooldowns[title]) < cooldown_seconds:
            return 

        self._cooldowns[title] = now
        emails = self._get_authorized_emails()

        # 1. Save to History for each user (so they can delete/dismiss individually)
        try:
            for email in emails:
                self.db.collection('users').document(email).collection('notifications').add({
                    'title': title,
                    'message': message,
                    'type': alert_type,
                    'isRead': False,
                    'timestamp': firestore.SERVER_TIMESTAMP,
                    'robotId': self.robot_id
                })
        except Exception as e:
            logger.error(f"Erro ao salvar histórico: {e}")

        # 2. Send Real-time Push Notification via FCM
        try:
            msg = messaging.Message(
                notification=messaging.Notification(
                    title=f"🤖 {title}",
                    body=message
                ),
                topic=self.topic_name,
                android=messaging.AndroidConfig(priority='high')
            )
            messaging.send(msg)
            logger.info(f"🔔 Notificação enviada: {title}")
        except Exception as e:
            logger.error(f"Erro ao enviar FCM: {e}")