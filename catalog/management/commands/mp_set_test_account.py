"""Carga el access token de una cuenta vendedora de PRUEBA de Mercado Pago como la cuenta del boliche."""

import sys
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog import crypto, mp
from catalog.models import MercadoPagoAccount, Venue, VenueIntegration
from catalog.mp_connect import mark_connected  # sincroniza la VenueIntegration; no se modifica

# Más que el umbral de renovación (30 días): get_access_token no intenta renovar un token sin refresh.
VALID_FOR = timedelta(days=150)


class Command(BaseCommand):
    help = "Herramienta de pruebas: carga el token de una cuenta de prueba de Mercado Pago (se lee de la entrada estándar)."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Slug del boliche.")

    def handle(self, *args, **options):
        if not settings.MP_TEST_TOKENS:
            raise CommandError("MP_TEST_TOKENS no está activo: este comando solo sirve en el entorno de pruebas.")
        if not settings.MP_TOKEN_ENCRYPTION_KEY:
            raise CommandError("MP_TOKEN_ENCRYPTION_KEY no está configurada.")
        token = sys.stdin.readline().strip()
        if not token:
            raise CommandError("La entrada estándar está vacía: pasá el access token por stdin (< archivo).")
        venue = Venue.objects.filter(slug=options["slug"]).first()
        if venue is None:
            raise CommandError(f"No existe el boliche '{options['slug']}'.")
        try:
            user = mp.get_user(token)
        except mp.MercadoPagoError:
            raise CommandError("Mercado Pago rechazó el token.") from None
        if "test_user" not in (user.get("tags") or []):
            raise CommandError("Esa cuenta no es una cuenta de prueba de Mercado Pago.")
        try:
            mp_user_id = int(user["id"])
            encrypted, marker = crypto.encrypt(token), crypto.encrypt("")
        except (KeyError, TypeError, ValueError, crypto.TokenCryptoError):
            raise CommandError("Mercado Pago devolvió una cuenta inválida o la clave de cifrado no sirve.") from None
        nickname = user.get("nickname") or ""
        with transaction.atomic():
            account = MercadoPagoAccount.objects.filter(venue=venue).first() or MercadoPagoAccount(venue=venue)
            account.mp_user_id = mp_user_id
            account.access_token = encrypted
            account.refresh_token = marker  # este atajo no tiene refresh token
            account.public_key = ""
            account.live_mode = False
            account.needs_reconnect = False
            account.expires_at = timezone.now() + VALID_FOR
            account.save()
            mark_connected(account)
            VenueIntegration.objects.filter(venue=venue, provider=VenueIntegration.Provider.MERCADOPAGO).update(
                account_label=f"{nickname} (cuenta de prueba)",
            )
        self.stdout.write(f"{venue.slug}: cuenta de prueba {mp_user_id} ({nickname}) cargada.")
