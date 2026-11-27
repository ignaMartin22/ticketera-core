"""Anulación de entradas (devolución, contracargo o a mano).

Una entrada anulada deja de servir en portería (`door.scan`) y queda a la vista en el pedido
(`voidedAt`). Anular no libera cupo: `TicketType.sold` no se toca. El registro queda: nada se borra.
"""

import logging
from typing import NamedTuple

from django.db import transaction
from django.utils import timezone

from .models import Ticket, VoidReason

logger = logging.getLogger(__name__)


class VoidResult(NamedTuple):
    voided: int  # entradas que se anularon ahora
    used: int  # de esas, las que ya habían entrado (se anulan igual: queda el registro)


def void_tickets(tickets, reason, by=None):
    """Anula las entradas del queryset que todavía no lo están. Idempotente.

    UPDATE condicional (`voided_at IS NULL`): dos anulaciones simultáneas no pisan el motivo ni
    la fecha de la primera. Devuelve `VoidResult(voided, used)`.
    """
    if reason not in VoidReason.values:
        raise ValueError(f"motivo de anulación inválido: {reason}")
    now = timezone.now()
    with transaction.atomic():
        candidates = list(tickets.filter(voided_at__isnull=True).values_list("pk", flat=True))
        # Con las filas bloqueadas, lo que se cuenta es lo que el UPDATE condicional anula.
        rows = list(
            Ticket.objects.select_for_update()
            .filter(pk__in=candidates, voided_at__isnull=True)
            .values_list("pk", "used_at")
        )
        voided = Ticket.objects.filter(pk__in=[pk for pk, _ in rows], voided_at__isnull=True).update(
            voided_at=now, voided_by=by, void_reason=reason,
        )
    used = sum(1 for _, used_at in rows if used_at is not None)
    if used:
        logger.warning("Entradas anuladas que ya habían ingresado: %s de %s (motivo %s).", used, voided, reason)
    return VoidResult(voided, used)
