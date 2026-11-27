"""Límite de intentos para el login del /admin/ de Django y de /plataforma/entrar/.

Bloquea 15 minutos con 5 fallos por (usuario, IP) o 20 por IP. Los fallos se guardan en
`DoorLoginFailure` con `scope="admin"`, aparte de los de portería. Mientras está bloqueado, el
login responde con el mismo formulario y un error genérico, igual exista o no el usuario.
"""

from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .client import proxied_client_ip
from .models import DoorLoginFailure
from .staff_auth import clear_login_failures, login_retry_after, record_login_failure

SCOPE = DoorLoginFailure.Scope.ADMIN
MAX_FAILURES = 5
MAX_FAILURES_PER_IP = 20
LOCKED_MESSAGE = "Demasiados intentos. Probá en unos minutos."


class LimitedLoginMixin:
    def clean(self):
        username = self.cleaned_data.get("username")
        key = username.strip().lower()[:150] if isinstance(username, str) else ""
        ip = proxied_client_ip(self.request) if self.request is not None else ""
        if login_retry_after(key, ip, SCOPE, MAX_FAILURES, MAX_FAILURES_PER_IP):
            raise ValidationError(LOCKED_MESSAGE, code="locked")
        try:
            cleaned = super().clean()
        except ValidationError:
            if key:
                record_login_failure(key, ip, SCOPE)
            raise
        clear_login_failures(key, ip, SCOPE)
        return cleaned


class LimitedAuthenticationForm(LimitedLoginMixin, AuthenticationForm):
    pass


class LimitedAdminAuthenticationForm(LimitedLoginMixin, AdminAuthenticationForm):
    pass
