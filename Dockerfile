FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --system --uid 10001 --create-home app

WORKDIR /app

# Dependencias en su propia capa: solo se reinstalan si cambia requirements.txt.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# Valores de relleno SOLO para este paso del build; no quedan en el entorno de la imagen.
RUN DJANGO_SECRET_KEY=build-only-placeholder \
    POSTGRES_DB=build POSTGRES_USER=build POSTGRES_PASSWORD=build \
    python manage.py collectstatic --noinput \
    && chown -R app:app /app/staticfiles \
    && mkdir -p /app/media && chown app:app /app/media

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8000), 3)"]

CMD ["gunicorn", "ticketera.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30", "--max-requests", "500", "--max-requests-jitter", "50", "--access-logfile", "-", "--error-logfile", "-"]
