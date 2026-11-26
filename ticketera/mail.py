"""Backend de mail con proveedor de respaldo.

Manda cada mail por el SMTP principal (``EMAIL_*``). Si falla (cupo agotado, caída, credenciales),
lo reintenta en el momento por el de respaldo (``EMAIL_FALLBACK_*``). Si fallan los dos, el error
sigue su camino como con el backend SMTP de Django: el mail de entradas queda pendiente para
``manage.py send_ticket_emails``.
"""

import logging

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

logger = logging.getLogger(__name__)


def fallback_backend():
    return SMTPBackend(
        host=settings.EMAIL_FALLBACK_HOST,
        port=settings.EMAIL_FALLBACK_PORT,
        username=settings.EMAIL_FALLBACK_HOST_USER,
        password=settings.EMAIL_FALLBACK_HOST_PASSWORD,
        use_tls=settings.EMAIL_FALLBACK_USE_TLS,
        use_ssl=False,
        fail_silently=False,
    )


class FailoverBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.primary = SMTPBackend(fail_silently=False, **kwargs)
        self.fallback = fallback_backend() if settings.EMAIL_FALLBACK_HOST else None

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                sent += self.send_one(message)
            except Exception:
                if not self.fail_silently:
                    raise
        return sent

    def send_one(self, message):
        try:
            return self.primary.send_messages([message])
        except Exception:
            if self.fallback is None:
                raise
            # WARNING para que se vea en el log de `web` sin configurar logging.
            logger.warning(
                "Falló el SMTP principal; mando por el respaldo el mail a %s (%s)",
                ", ".join(message.recipients()), message.subject, exc_info=True,
            )
        return self.fallback.send_messages([message])
