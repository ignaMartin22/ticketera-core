# Setup local (Linux/bash)

PostgreSQL corre en Docker (no hay instalación local):

```bash
docker start ticketera-db   # o, si no existe:
docker run -d --name ticketera-db -e POSTGRES_DB=ticketera -e POSTGRES_USER=ticketera -e POSTGRES_PASSWORD=ticketera -p 5432:5432 postgres:16-alpine
```

Las variables se leen de `.env` (creado desde `.env.example`, gitignored). `load_dotenv` no pisa variables reales del entorno: los `export` de shell tienen prioridad.

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
.venv/bin/python manage.py test catalog backoffice
```

El usuario `ticketera` de la base local es superuser de Postgres (lo crea el contenedor), así que los tests corren sin configuración extra. Si `DATABASE_URL` apunta a Neon, los tests crearían `test_<db>` en Neon: lento. Conviene correrlos contra la base local (sin `DATABASE_URL`).

Datos de prueba: `.venv/bin/python manage.py seed_infinity` (idempotente: `update_or_create`). Ojo: re-correrlo **pisa `sold` con los valores demo** — solo antes de producción.

## Neon

- Proyecto `ticketera-core` (id `shy-firefly-30702550`), región `aws-sa-east-1`, pg 18, rama `main`, base y rol `ticketera`. Ya está migrado y seedeado.
- La org de Neon la maneja Vercel: crear proyectos por API Neon está bloqueado. Se creó con `vercel integration add neon --name ticketera-core -m region=gru1 -m auth=false --no-connect --no-env-pull --no-claim` (Vercel CLI autenticado). `NEON_API_KEY` en `.env` sirve para el resto de operaciones API (databases, roles, connection URIs).
- `DATABASE_URL` en `.env` → se ignoran `POSTGRES_*`. El código suma `conn_max_age=600`, `conn_health_checks`, `DISABLE_SERVER_SIDE_CURSORS` (recomendado para el pooler PgBouncer de Neon) y fuerza `sslmode=require` si la URL no trae un `sslmode` (respeta `verify-full`/`verify-ca`).
- Bootstrap de una base Neon vacía (endpoint directo, sin `-pooler`): `migrate` → `createsuperuser` → `seed_infinity`. Sin el superusuario no se puede entrar a `/plataforma/` ni `/admin/`. Membresías de equipo se cargan después desde `/plataforma/boliches/<slug>/equipo/`.
- `migrate`/`seed`/`createsuperuser`: endpoint directo (sin `-pooler`). App: cualquiera de los dos.

## Producción

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
gunicorn ticketera.wsgi
```

Whitenoise sirve los estáticos con hash en el nombre: `collectstatic` es requisito del deploy. Variables mínimas: `DATABASE_URL`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` (con esquema `https://`) y `DJANGO_BEHIND_PROXY=1` detrás del proxy HTTPS — nunca sin un proxy que sanee `X-Forwarded-Proto`. `DJANGO_SSL_REDIRECT`, `DJANGO_HSTS_SECONDS` y `DJANGO_HSTS_PRELOAD` son opt-in (ver `.env.example`).

## TicketJuy (frontend)

- `TicketJuy/` está clonado dentro de este repo (untracked). Consume la API solo desde el servidor de Next → **no necesita CORS**. Única variable: `TICKETERA_API_URL=http://127.0.0.1:8000` en su `.env.local`. Los assets (`/brand/`, `/venues/`) los sirve el front desde su `public/`; la API solo devuelve paths.
