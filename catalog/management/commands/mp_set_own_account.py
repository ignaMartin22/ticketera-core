"""Carga un access token como cuenta de cobro directo de un boliche (sin marketplace_fee)."""

import sys
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog import crypto, mp
from catalog.models import MercadoPagoAccount, Venue, VenueIntegration
from catalog.mp_connect import mark_connected  # sincroniza la VenueIntegration; no se modifica

# Solo para cumplir el campo: en modo directo `expires_at` no se usa (no hay renovación ni vencimiento).
VALID_FOR = timedelta(days=3650)


def load_direct_account(slug, production, real_error=None):
    """Valida el token de stdin y deja la cuenta directa del boliche. Devuelve (venue, id, nickname, es_prueba)."""
    if not settings.MP_TOKEN_ENCRYPTION_KEY:
        raise CommandError("MP_TOKEN_ENCRYPTION_KEY no está configurada.")
    token = sys.stdin.readline().strip()
    if not token:
        raise CommandError("La entrada estándar está vacía: pasá el access token por stdin (< archivo).")
    venue = Venue.objects.filter(slug=slug).first()
    if venue is None:
        raise CommandError(f"No existe el boliche '{slug}'.")
    try:
        user = mp.get_user(token)
    except mp.MercadoPagoError:
        raise CommandError("Mercado Pago rechazó el token.") from None
    is_test = "test_user" in (user.get("tags") or [])
    if is_test and production:
        raise CommandError("Esa cuenta es de prueba; sacá --produccion.")
    if not is_test and not production:
        raise CommandError(real_error or "Esa es una cuenta real de Mercado Pago. Para cargarla usá --produccion.")
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
        account.refresh_token = marker  # las credenciales propias no tienen refresh token
        account.public_key = ""
        account.live_mode = not is_test
        account.direct = True
        account.needs_reconnect = False
        account.expires_at = timezone.now() + VALID_FOR
        account.save()
        mark_connected(account)
        label = f"{nickname} (cobro directo{', cuenta de prueba' if is_test else ''})"
        VenueIntegration.objects.filter(venue=venue, provider=VenueIntegration.Provider.MERCADOPAGO).update(
            account_label=label,
        )
    return venue, mp_user_id, nickname, is_test


class Command(BaseCommand):
    help = (
        "Carga el access token (por entrada estándar) como cuenta de cobro directo del boliche: "
        "toda la venta entra a esa cuenta, sin marketplace_fee."
    )

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Slug del boliche.")
        parser.add_argument(
            "--produccion", action="store_true", help="Necesario para cargar una cuenta real (no de prueba).",
        )

    def handle(self, *args, **options):
        venue, mp_user_id, nickname, is_test = load_direct_account(options["slug"], options["produccion"])
        kind = "de prueba" if is_test else "real"
        self.stdout.write(f"{venue.slug}: cuenta de cobro directo {mp_user_id} ({nickname}), {kind}.")
