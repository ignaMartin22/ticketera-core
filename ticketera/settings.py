import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
SECRET_KEY = (
    os.environ.get("DJANGO_SECRET_KEY", "django-insecure-local-development-only")
    if DEBUG
    else os.environ["DJANGO_SECRET_KEY"]
)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
ROOT_URLCONF = "ticketera.urls"
WSGI_APPLICATION = "ticketera.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Jujuy"
USE_TZ = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
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
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

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
