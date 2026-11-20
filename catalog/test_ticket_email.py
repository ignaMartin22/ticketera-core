from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Order
from .qr import sign_ticket
from .test_tickets import make_event, make_order, make_type
from .tickets import issue_tickets

FRONT = "https://front.example.com"


@override_settings(FRONT_BASE_URL=FRONT, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TicketEmailTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.general = make_type(self.event, "general")
        self.vip = make_type(self.event, "vip")
        self.order = make_order(self.event, [(self.general, 2), (self.vip, 1)])

    def issue(self, order=None):
        with self.captureOnCommitCallbacks(execute=True):
            issue_tickets((order or self.order).pk)

    def test_paid_order_emails_one_qr_per_ticket(self):
        self.issue()

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["b@example.com"])
        self.assertEqual(message.subject, "Tus entradas para Fecha")
        url = f"{FRONT}/moon/fecha/pedido/{self.order.pk}"
        self.assertIn(url, message.body)
        html = message.alternatives[0].content
        self.assertIn(url, html)
        images = [part for part in message.attachments if part.get_content_type() == "image/png"]
        self.assertEqual(len(images), 3)
        for number, image in enumerate(images, start=1):
            self.assertEqual(image["Content-ID"], f"<entrada-{number}@ticketera>")
            self.assertIn(f"cid:entrada-{number}@ticketera", html)
            self.assertTrue(image.get_payload(decode=True).startswith(b"\x89PNG"))
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.tickets_emailed_at)

    def test_qr_carries_the_signed_ticket_token(self):
        with patch("catalog.ticket_email.qr_png", return_value=b"\x89PNG") as qr_png:
            self.issue()
        codes = [call.args[0] for call in qr_png.call_args_list]
        self.assertEqual(codes, [sign_ticket(ticket.pk) for ticket in self.order.tickets.order_by("created_at", "pk")])

    def test_issuing_again_does_not_resend(self):
        self.issue()
        self.issue()
        self.assertEqual(len(mail.outbox), 1)

    def test_no_mail_when_issuance_fails(self):
        expired = make_order(self.event, [(self.general, 11)], expires_in=-timedelta(minutes=1))
        with self.assertRaises(Exception), self.captureOnCommitCallbacks(execute=True):
            issue_tickets(expired.pk)
        self.assertEqual(mail.outbox, [])

    def test_send_failure_keeps_the_payment_and_leaves_it_pending(self):
        with patch("catalog.ticket_email.EmailMultiAlternatives.send", side_effect=OSError("smtp caído")):
            with self.assertLogs("catalog.ticket_email", "ERROR"):
                self.issue()
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual(self.order.tickets.count(), 3)
        self.assertIsNone(self.order.tickets_emailed_at)

    def test_command_retries_pending_orders_only(self):
        with patch("catalog.ticket_email.EmailMultiAlternatives.send", side_effect=OSError):
            with self.assertLogs("catalog.ticket_email", "ERROR"):
                self.issue()
        recent = make_order(self.event, [(self.general, 1)])
        with patch("catalog.ticket_email.send_after_commit"):
            issue_tickets(recent.pk)  # recién pagado: su envío sale con la confirmación
        Order.objects.filter(pk=self.order.pk).update(paid_at=timezone.now() - timedelta(minutes=10))
        unpaid = make_order(self.event, [(self.vip, 1)])

        out = StringIO()
        call_command("send_ticket_emails", stdout=out)
        self.assertIn("1 enviados, 0 con error", out.getvalue())
        self.assertEqual([m.to for m in mail.outbox], [["b@example.com"]])
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.tickets_emailed_at)
        for order in (recent, unpaid):
            order.refresh_from_db()
            self.assertIsNone(order.tickets_emailed_at)

        call_command("send_ticket_emails", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(FRONT_BASE_URL="")
    def test_without_front_url_the_mail_still_has_the_qrs(self):
        self.issue()
        message = mail.outbox[0]
        self.assertNotIn("pedido/", message.body)
        self.assertEqual(len(message.attachments), 3)

    def test_free_order_is_emailed_when_reserved(self):
        free = make_type(self.event, "free", price="0")
        response = None
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("order-create", args=["moon", "fecha"]),
                {"email": "gratis@example.com", "name": "Ana", "items": [{"ticketTypeId": free.slug, "quantity": 1}]},
                content_type="application/json",
                headers={"Idempotency-Key": "8a4f0c1e-6a52-4d3f-9a3b-0d2b9c1f7e11"},
            )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual([m.to for m in mail.outbox], [["gratis@example.com"]])

    def test_admin_resend_action_sends_again(self):
        self.issue()
        root = get_user_model().objects.create_superuser("root", "root@example.com", "x")
        self.client.force_login(root)
        response = self.client.post(
            reverse("admin:catalog_order_changelist"),
            {"action": "resend_ticket_email", "_selected_action": [str(self.order.pk)]},
            follow=True,
        )
        self.assertContains(response, "Entradas reenviadas en 1 pedido(s).")
        self.assertEqual(len(mail.outbox), 2)
