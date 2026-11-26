from smtplib import SMTPDataError
from unittest.mock import patch

from django.core.mail import EmailMessage, get_connection
from django.test import SimpleTestCase, TestCase, override_settings

from .test_tickets import make_event, make_order, make_type
from .ticket_email import send_ticket_email
from .tickets import issue_tickets

PRIMARY = "smtp.principal.test"
FALLBACK = "smtp.respaldo.test"
QUOTA = SMTPDataError(550, b"daily quota exceeded")

SMTP = {
    "EMAIL_HOST": PRIMARY, "EMAIL_FALLBACK_HOST": FALLBACK,
    "EMAIL_BACKEND": "ticketera.mail.FailoverBackend",
}


class FakeSMTP:
    """Reemplaza el envío SMTP real: cada host falla o anota los mails que "mandó"."""

    def __init__(self, failing=None):
        # host -> excepción, o función que recibe los mails y devuelve la excepción (o None).
        self.failing = failing or {}
        self.sent = []

    def __call__(self, backend, messages):
        failure = self.failing.get(backend.host)
        if callable(failure) and not isinstance(failure, BaseException):
            failure = failure(messages)
        if failure is not None:
            raise failure
        self.sent += [(backend.host, m.to[0]) for m in messages]
        return len(messages)

    def patch(self):
        return patch(
            "django.core.mail.backends.smtp.EmailBackend.send_messages",
            autospec=True, side_effect=self,
        )


def message(to="ana@example.com"):
    return EmailMessage(subject="Tus entradas", body="QR", to=[to])


@override_settings(**SMTP)
class FailoverBackendTests(SimpleTestCase):
    def send(self, smtp, *messages, fail_silently=False):
        with smtp.patch():
            return get_connection(fail_silently=fail_silently).send_messages(list(messages))

    def test_sends_through_the_primary_when_it_works(self):
        smtp = FakeSMTP()
        self.assertEqual(self.send(smtp, message()), 1)
        self.assertEqual(smtp.sent, [(PRIMARY, "ana@example.com")])

    def test_falls_back_when_the_primary_fails(self):
        smtp = FakeSMTP({PRIMARY: QUOTA})
        with self.assertLogs("ticketera.mail", "WARNING") as logs:
            self.assertEqual(self.send(smtp, message()), 1)
        self.assertEqual(smtp.sent, [(FALLBACK, "ana@example.com")])
        self.assertIn("ana@example.com", logs.output[0])

    def test_raises_when_both_fail(self):
        smtp = FakeSMTP({PRIMARY: QUOTA, FALLBACK: OSError("respaldo caído")})
        with self.assertLogs("ticketera.mail", "WARNING"), self.assertRaises(OSError):
            self.send(smtp, message())
        self.assertEqual(smtp.sent, [])

    def test_fail_silently_swallows_the_error_when_both_fail(self):
        smtp = FakeSMTP({PRIMARY: QUOTA, FALLBACK: OSError})
        with self.assertLogs("ticketera.mail", "WARNING"):
            self.assertEqual(self.send(smtp, message(), fail_silently=True), 0)

    def test_only_the_failing_message_goes_to_the_fallback(self):
        refuse_beto = lambda messages: QUOTA if messages[0].to == ["beto@example.com"] else None
        smtp = FakeSMTP({PRIMARY: refuse_beto})
        with self.assertLogs("ticketera.mail", "WARNING"):
            sent = self.send(smtp, message("ana@example.com"), message("beto@example.com"))
        self.assertEqual(sent, 2)
        self.assertEqual(smtp.sent, [(PRIMARY, "ana@example.com"), (FALLBACK, "beto@example.com")])

    @override_settings(EMAIL_FALLBACK_HOST="")
    def test_without_fallback_the_primary_error_propagates(self):
        smtp = FakeSMTP({PRIMARY: QUOTA})
        with self.assertRaises(SMTPDataError):
            self.send(smtp, message())
        self.assertEqual(smtp.sent, [])


@override_settings(**SMTP, FRONT_BASE_URL="https://front.example.com")
class TicketEmailFailoverTests(TestCase):
    def setUp(self):
        event = make_event()
        self.order = make_order(event, [(make_type(event, "general"), 1)])
        with patch("catalog.ticket_email.send_after_commit"):
            issue_tickets(self.order.pk)

    def test_ticket_email_goes_out_through_the_fallback(self):
        smtp = FakeSMTP({PRIMARY: QUOTA})
        with smtp.patch(), self.assertLogs("ticketera.mail", "WARNING"):
            self.assertTrue(send_ticket_email(self.order.pk))
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.tickets_emailed_at)
        self.assertEqual(smtp.sent, [(FALLBACK, "b@example.com")])

    def test_order_stays_pending_when_both_fail(self):
        smtp = FakeSMTP({PRIMARY: QUOTA, FALLBACK: OSError})
        with smtp.patch(), self.assertLogs("ticketera.mail", "WARNING"):
            with self.assertLogs("catalog.ticket_email", "ERROR"):
                self.assertFalse(send_ticket_email(self.order.pk))
        self.order.refresh_from_db()
        self.assertIsNone(self.order.tickets_emailed_at)
