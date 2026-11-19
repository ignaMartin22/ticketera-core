from django.test import TestCase
from django.urls import reverse


class AdminLoginPageTests(TestCase):
    def test_login_page_renders_for_anonymous_user(self):
        # La pantalla de login arma la lista de apps y pregunta los permisos del usuario anónimo.
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')
