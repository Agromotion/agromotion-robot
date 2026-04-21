import logging
import time
import re
from firebase_admin import firestore, messaging

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, db, robot_id):
        self.db = db
        self.robot_id = robot_id
        clean_id = re.sub(r'[^a-zA-Z0-9-_]', '_', self.robot_id)
        self.topic_name = f"robot_{clean_id}"
        self._cooldowns = {}  # {event_title: last_sent_time}

    def _get_authorized_emails(self):
        """Busca a lista de emails autorizados a receber alertas."""
        try:
            docs = self.db.collection('authorized_emails').stream()
            return [doc.id for doc in docs]
        except Exception as e:
            logger.error(f"Erro ao buscar emails autorizados: {e}")
            return []

    def broadcast_alert(self, title, message, alert_type="info", cooldown_seconds=300):
        """
        Envia notificações push e guarda no histórico dos utilizadores.
        cooldown_seconds: evita spam de alertas repetidos (ex: GPS a saltar).
        """
        now = time.time()
        if title in self._cooldowns and (now - self._cooldowns[title]) < cooldown_seconds:
            return 

        self._cooldowns[title] = now
        emails = self._get_authorized_emails()
        
        if not emails:
            logger.warning(f"Nenhum utilizador autorizado para enviar alerta: {title}")
            return

        # Guardar no Histórico de cada utilizador via Batch
        try:
            batch = self.db.batch()
            for email in emails:
                notif_ref = self.db.collection('users').document(email).collection('notifications').document()
                batch.set(notif_ref, {
                    'title': title,
                    'message': message,
                    'type': alert_type,
                    'isRead': False,
                    'timestamp': firestore.SERVER_TIMESTAMP,
                    'robotId': self.robot_id
                })
            batch.commit()
            logger.debug(f"Histórico de notificações atualizado para {len(emails)} utilizadores.")
        except Exception as e:
            logger.error(f"Erro ao salvar histórico no Firestore: {e}")

        # Enviar Notificação Push em Tempo Real via FCM (Tópico)
        try:
            # Configuração visual por tipo de alerta
            emoji = "⚠️" if alert_type == "warning" else "🚨" if alert_type == "error" else "🤖"
            
            msg = messaging.Message(
                notification=messaging.Notification(
                    title=f"{emoji} {title}",
                    body=message
                ),
                # Payload de dados para a lógica interna da App
                data={
                    "robotId": self.robot_id,
                    "type": alert_type,
                    "click_action": "FLUTTER_NOTIFICATION_CLICK"
                },
                topic=self.topic_name,
                android=messaging.AndroidConfig(
                    priority='high',
                    notification=messaging.AndroidNotification(
                        sound='default',
                        click_action='FLUTTER_NOTIFICATION_CLICK'
                    )
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(sound='default')
                    )
                )
            )
            messaging.send(msg)
            logger.info(f"Notificação Push enviada para o tópico {self.topic_name}: {title}")
        except Exception as e:
            logger.error(f"Erro ao enviar FCM: {e}")