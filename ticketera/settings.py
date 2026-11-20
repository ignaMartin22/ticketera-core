import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
SECRET_KEY = (
    os.environ.get("DJANGO_SECRET_KEY", "django-insecure-local-development-only")
    if DEBUG
    else os.environ["DJANGO_SECRET_KEY"]
)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
ROOT_URLCONF = "ticketera.urls"
WSGI_APPLICATION = "ticketera.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Jujuy"
USE_TZ = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SSL_REDIRECT") == "1"
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))
if SECURE_HSTS_SECONDS > 0:
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = os.environ.get("DJANGO_HSTS_PRELOAD") == "1"
# Solo activar detrás de un proxy que termina TLS y sanea X-Forwarded-Proto.
if os.environ.get("DJANGO_BEHIND_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "catalog",
    "backoffice",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
LOGIN_URL = "backoffice:login"
LOGIN_REDIRECT_URL = "backoffice:venue-list"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # El manifiesto de whitenoise exige collectstatic: en desarrollo y tests se sirven los archivos tal cual.
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.parse(
            os.environ["DATABASE_URL"], conn_max_age=600, conn_health_checks=True,
            disable_server_side_cursors=True,
        )
    }
    DATABASES["default"].setdefault("OPTIONS", {}).setdefault("sslmode", "require")
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ["POSTGRES_USER"],
            "PASSWORD": os.environ["POSTGRES_PASSWORD"],
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "OPTIONS": {"sslmode": os.environ.get("POSTGRES_SSLMODE", "prefer")},
        },
    }

# Mercado Pago (conexión del boliche). Todo es opcional: sin estas variables la app
# arranca y las rutas de conexión responden 503 mp_not_configured.
MP_APP_ID = os.environ.get("MP_APP_ID", "")
MP_CLIENT_SECRET = os.environ.get("MP_CLIENT_SECRET", "")
MP_REDIRECT_URI = os.environ.get("MP_REDIRECT_URI", "")
MP_TOKEN_ENCRYPTION_KEY = os.environ.get("MP_TOKEN_ENCRYPTION_KEY", "")
MP_CONNECT_RETURN_URL = os.environ.get("MP_CONNECT_RETURN_URL", "/")
MP_TEST_TOKENS = os.environ.get("MP_TEST_TOKENS") == "1"
PUBLIC_API_URL = os.environ.get("PUBLIC_API_URL", "").rstrip("/")
FRONT_BASE_URL = os.environ.get("FRONT_BASE_URL", "").rstrip("/")
# Mail de entradas al comprador. Sin DJANGO_EMAIL_HOST, en desarrollo los mails se imprimen en la
# consola; en el servidor no salen y quedan pendientes para `manage.py send_ticket_emails`.
EMAIL_HOST = os.environ.get("DJANGO_EMAIL_HOST", "")
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST or not DEBUG
    else "django.core.mail.backends.console.EmailBackend",
)
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("DJANGO_EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "TicketJuy <entradas@localhost>")
# Clave secreta de la aplicación para validar la firma del webhook de pagos.
# Sin ella el webhook responde 503 mp_not_configured.
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "")
