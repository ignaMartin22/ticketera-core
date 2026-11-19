import re
import uuid

from django.test import SimpleTestCase, override_settings

from catalog.qr import InvalidTicketToken, sign_ticket, verify_ticket


class QrFirmadoTests(SimpleTestCase):
    def test_ida_y_vuelta(self):
        ticket_id = uuid.uuid4()
        self.assertEqual(verify_ticket(sign_ticket(ticket_id)), ticket_id)

    def test_determinismo(self):
        ticket_id = uuid.uuid4()
        self.assertEqual(sign_ticket(ticket_id), sign_ticket(ticket_id))

    def test_formato(self):
        token = sign_ticket(uuid.uuid4())
        self.assertRegex(token, r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
        self.assertLessEqual(len(token), 80)

    def test_firma_alterada(self):
        token = sign_ticket(uuid.uuid4())
        pos = len(token) - 1
        cambio = "A" if token[pos] != "A" else "B"
        with self.assertRaises(InvalidTicketToken):
            verify_ticket(token[:pos] + cambio)

    def test_id_alterado(self):
        _, firma = sign_ticket(uuid.uuid4()).split(".")
        otro_id, _ = sign_ticket(uuid.uuid4()).split(".")
        with self.assertRaises(InvalidTicketToken):
            verify_ticket(f"{otro_id}.{firma}")

    def test_basura(self):
        valido = sign_ticket(uuid.uuid4())
        basura = ["", "abc", ".", "a.b", valido.replace(".", " . "), "a" * 500, None, 123]
        for valor in basura:
            with self.subTest(valor=valor):
                with self.assertRaises(InvalidTicketToken):
                    verify_ticket(valor)

    def test_otra_secret_key(self):
        token = sign_ticket(uuid.uuid4())
        with override_settings(SECRET_KEY="otra-clave-distinta"):
            with self.assertRaises(InvalidTicketToken):
                verify_ticket(token)
