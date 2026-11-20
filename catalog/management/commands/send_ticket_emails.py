from django.core.management.base import BaseCommand

from catalog.ticket_email import pending_orders, send_ticket_email


class Command(BaseCommand):
    help = "Manda el mail de entradas a los pedidos pagados que todavía no lo recibieron."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200, help="Máximo de pedidos por corrida.")

    def handle(self, *args, **options):
        order_ids = list(pending_orders().values_list("pk", flat=True)[: options["limit"]])
        sent = sum(send_ticket_email(order_id) for order_id in order_ids)
        failed = len(order_ids) - sent
        self.stdout.write(f"Mails de entradas: {sent} enviados, {failed} con error.")
