from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from catalog import gateways
from catalog.models import Order
from catalog.payment_confirm import sync_order

WINDOW = timedelta(hours=48)


class Command(BaseCommand):
    help = "Confirma los pagos de pedidos con link (Mercado Pago, Macro o Nave) que no recibieron aviso (cron cada pocos minutos)."

    def handle(self, *args, **options):
        pending = (
            Order.objects.filter(paid_at__isnull=True, created_at__gte=timezone.now() - WINDOW)
            .filter(~Q(mp_order_id="") | Q(gateway_charges__isnull=False))
            .distinct()
            .select_related("event__venue")
        )
        reviewed = confirmed = failed = 0
        for order in list(pending):
            reviewed += 1
            try:
                results = [sync_order(order)]
                if results[0] != "confirmed":
                    results.append(gateways.sync_order(order))
                result = next((r for r in ("confirmed", "failed") if r in results), "pending")
            except Exception as exc:  # un pedido roto no corta los demás
                result = "failed"
                self.stderr.write(f"pedido {order.pk}: error inesperado ({type(exc).__name__})")
            if result == "confirmed":
                confirmed += 1
            elif result == "failed":
                failed += 1
        self.stdout.write(f"Revisados: {reviewed}. Confirmados: {confirmed}. Fallidos: {failed}.")
