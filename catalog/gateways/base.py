"""Lo común a las pasarelas sin reparto: credenciales, cobro por pedido y confirmación.

Reglas (las mismas que con Mercado Pago, ver `payment_confirm`): nunca se confía en el aviso
(siempre se consulta el cobro con las credenciales del boliche), un cobro emite las entradas una
sola vez y lo que no se puede emitir queda "a revisar" para que el boliche lo resuelva a mano.
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction

from .. import crypto
from ..models import GatewayAccount, GatewayCharge, Order, Payment
from ..payment_confirm import approved_outcome

CENT = Decimal("0.01")
Outcome = Payment.Outcome
logger = logging.getLogger(__name__)


class GatewayError(Exception):
    """La pasarela respondió con un error, algo inesperado, o no respondió."""


def money(value):
    """Decimal a 2 decimales, o None si no es un monto válido."""
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() and abs(amount) < Decimal(10) ** 10 else None


def credentials(account):
    """Las credenciales descifradas, como diccionario. Nunca van a logs ni respuestas."""
    data = json.loads(crypto.decrypt(account.credentials))
    if not isinstance(data, dict):
        raise crypto.TokenCryptoError("credenciales con formato inválido")
    return data


def save_account(venue, provider, data, live_mode):
    """Crea o reemplaza la cuenta del boliche en una pasarela, con las credenciales cifradas."""
    account, _created = GatewayAccount.objects.update_or_create(
        venue=venue, provider=provider,
        defaults={"credentials": crypto.encrypt(json.dumps(data)), "live_mode": live_mode},
    )
    return account


def settle(charge_id, *, status, approved, amount, currency="ARS"):
    """Guarda lo que informó la pasarela y, si el cobro se aprobó, emite las entradas.

    `status`, `approved` y `amount` salen de consultar la pasarela, no del aviso.
    """
    with transaction.atomic():
        charge = GatewayCharge.objects.select_for_update().get(pk=charge_id)
        order = Order.objects.select_for_update(of=("self",)).get(pk=charge.order_id)
        if approved and not settings.PAYMENTS_ALLOW_TEST_MODE:
            account = GatewayAccount.objects.filter(
                venue_id=order.event.venue_id, provider=charge.provider,
            ).first()
            if account is None or not account.live_mode:
                logger.warning("Cobro aprobado de una cuenta no productiva: no emite (cobro %s).", charge.pk)
                approved = False
        charge.status = str(status or "")[:40]
        if amount is not None:
            charge.amount = amount
        if charge.outcome != Outcome.ISSUED:
            if not approved:
                charge.outcome = Outcome.NOT_APPROVED
            else:
                charge.outcome = approved_outcome(order, amount, currency)
        charge.save()
        return charge
