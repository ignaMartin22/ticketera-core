"""Carga el access token de una cuenta vendedora de PRUEBA de Mercado Pago como la cuenta del boliche.

Es un cobro directo en una cuenta de prueba: reutiliza la lógica de `mp_set_own_account`.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .mp_set_own_account import load_direct_account


class Command(BaseCommand):
    help = "Herramienta de pruebas: carga el token de una cuenta de prueba de Mercado Pago (se lee de la entrada estándar)."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Slug del boliche.")

    def handle(self, *args, **options):
        if not settings.MP_TEST_TOKENS:
            raise CommandError("MP_TEST_TOKENS no está activo: este comando solo sirve en el entorno de pruebas.")
        venue, mp_user_id, nickname, _ = load_direct_account(
            options["slug"], production=False, real_error="Esa cuenta no es una cuenta de prueba de Mercado Pago.",
        )
        self.stdout.write(f"{venue.slug}: cuenta de prueba {mp_user_id} ({nickname}) cargada.")
