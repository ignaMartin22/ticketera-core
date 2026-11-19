import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent
WHITENOISE_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


def load_settings(**env_extra):
    """Importa ticketera.settings en un proceso aparte con el entorno indicado
    (None borra la variable) y devuelve los valores que interesan como dict."""
    env = {
        **os.environ,
        "DJANGO_SECRET_KEY": "x",
        "POSTGRES_DB": "x",
        "POSTGRES_USER": "x",
        "POSTGRES_PASSWORD": "x",
    }
    for var in ("DJANGO_DEBUG", "DJANGO_CSRF_TRUSTED_ORIGINS", "DJANGO_BEHIND_PROXY"):
        env.pop(var, None)
    for key, value in env_extra.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    code = (
        "import json, ticketera.settings as s;"
        "print(json.dumps({'middleware': s.MIDDLEWARE,"
        "'csrf': s.CSRF_TRUSTED_ORIGINS,"
        "'proxy': getattr(s, 'SECURE_PROXY_SSL_HEADER', None),"
        "'storage': s.STORAGES['staticfiles']['BACKEND'] if hasattr(s, 'STORAGES') else None}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


class DeploySettingsTests(SimpleTestCase):
    def test_whitenoise_va_despues_de_security_sin_debug(self):
        cfg = load_settings()
        mw = cfg["middleware"]
        i = mw.index("django.middleware.security.SecurityMiddleware")
        self.assertEqual(mw[i + 1], "whitenoise.middleware.WhiteNoiseMiddleware")
        self.assertEqual(cfg["storage"], WHITENOISE_STORAGE)

    def test_con_debug_no_se_exige_manifiesto(self):
        cfg = load_settings(DJANGO_DEBUG="1")
        self.assertNotEqual(cfg["storage"], WHITENOISE_STORAGE)

    def test_csrf_trusted_origins_desde_la_variable(self):
        cfg = load_settings(DJANGO_CSRF_TRUSTED_ORIGINS="https://a.example, https://b.example,")
        self.assertEqual(cfg["csrf"], ["https://a.example", "https://b.example"])

    def test_csrf_trusted_origins_vacio_por_defecto(self):
        self.assertEqual(load_settings()["csrf"], [])

    def test_sin_proxy_no_se_define_el_header_ssl(self):
        self.assertIsNone(load_settings()["proxy"])
        self.assertIsNone(load_settings(DJANGO_BEHIND_PROXY="0")["proxy"])

    def test_con_proxy_se_define_el_header_ssl(self):
        cfg = load_settings(DJANGO_BEHIND_PROXY="1")
        self.assertEqual(cfg["proxy"], ["HTTP_X_FORWARDED_PROTO", "https"])
