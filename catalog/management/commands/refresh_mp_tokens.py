from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import MercadoPagoAccount
from catalog.mp_connect import RENEW_BEFORE, renew_account


class Command(BaseCommand):
    help = "Renueva los tokens de Mercado Pago a los que les quedan menos de 30 días (cron diario)."

    def handle(self, *args, **options):
        due = MercadoPagoAccount.objects.filter(
            needs_reconnect=False, expires_at__lt=timezone.now() + RENEW_BEFORE,
        ).values_list("pk", "venue__slug")
        renewed = failed = 0
        for pk, slug in list(due):
            try:
                _, ok = renew_account(pk)
            except Exception as exc:  # una cuenta rota no corta las demás
                ok = False
                self.stderr.write(f"{slug}: error inesperado ({type(exc).__name__})")
            if ok:
                renewed += 1
            else:
                failed += 1
                self.stderr.write(f"{slug}: no se pudo renovar, requiere reconexión")
        self.stdout.write(f"Renovadas: {renewed}. Fallidas: {failed}.")
