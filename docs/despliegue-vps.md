# Despliegue de pruebas en la VPS

Backend de TicketJuy (Django + PostgreSQL) en Docker, en `/opt/ticketjuy`, publicado solo en `127.0.0.1:8001`. Caddy (el del sistema) hace de proxy HTTPS desde `https://api.66-97-35-67.sslip.io`.

Todos los comandos se corren como un usuario con permiso para Docker (o con `sudo`), salvo que se indique otra cosa.

## 1. Requisitos y advertencias

- La VPS es chica y comparte servicios críticos. **No reiniciar la VPS. No tocar los otros servicios** (contenedores, Caddy existente, firewall).
- Caddy se **recarga** con `caddy reload`, **nunca** con `restart`.
- Todo puerto publicado por Docker va en `127.0.0.1` (Docker saltea el firewall). Este compose ya lo hace; no lo cambies.
- Requisitos: Docker y Compose v2 (`docker compose version`), `git`, Caddy en 80/443.
- Límites de memoria del proyecto: `web` 256 MB y `db` 256 MB.

**Medir la memoria ANTES de levantar** (paso previo obligatorio):

```bash
free -m
docker stats --no-stream
```

Mirá la columna `available` de `free -m`. **Si hay menos de 700 MB disponibles, no sigas**: avisá y esperá instrucciones.

## 2. Traer el código a `/opt/ticketjuy`

```bash
sudo mkdir -p /opt/ticketjuy && sudo chown "$USER": /opt/ticketjuy
```

Opción A: el repo es público (`https://github.com/ignaMartin22/ticketera-core`), clonar por HTTPS sin credenciales:

```bash
git clone --branch feat/mercado-pago https://github.com/ignaMartin22/ticketera-core.git /opt/ticketjuy
cd /opt/ticketjuy && git log --oneline -1
```

Opción B: el repo NO es público. Desde la máquina de desarrollo, empaquetar un commit fijo y copiarlo por `scp` (no hace falta ninguna credencial nueva en la VPS):

```bash
# En la máquina de desarrollo, dentro del repo:
git archive --format=tar.gz --prefix=ticketjuy/ -o ticketjuy.tar.gz <hash-del-commit>
scp ticketjuy.tar.gz <usuario>@66.97.35.67:/tmp/
# En la VPS:
tar -xzf /tmp/ticketjuy.tar.gz -C /opt/ticketjuy --strip-components=1
ls /opt/ticketjuy
rm /tmp/ticketjuy.tar.gz
```

Salida esperada: `ls` muestra `Dockerfile`, `deploy`, `manage.py`, `catalog`, etc. Con la opción B no hay `.git` en la VPS: para actualizar se repite el procedimiento con otro commit (ver paso 9).

## 3. Crear `deploy/.env`

```bash
cd /opt/ticketjuy
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

Generar los secretos y pegarlos en `deploy/.env` (cada uno reemplaza el valor `cambiar-por-...`):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # DJANGO_SECRET_KEY
openssl rand -hex 24                                              # POSTGRES_PASSWORD
docker run --rm python:3.13-slim sh -c "pip install -q cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # MP_TOKEN_ENCRYPTION_KEY
```

Completar también `MP_APP_ID`, `MP_CLIENT_SECRET` (de la app de Mercado Pago) y `FRONT_BASE_URL`. Dejar `DJANGO_DEBUG=0`.

`FRONT_BASE_URL` es a donde Mercado Pago devuelve al comprador después de pagar (`<FRONT_BASE_URL>/<boliche>/<evento>/pedido/<id>`). En el entorno de pruebas es el front de Railway: `https://ticketjuy-production.up.railway.app`. Con `http://localhost:3000` el pago se confirma igual, pero el comprador vuelve a su propia máquina y ve un 404. Después de cambiarla, recrear el contenedor `web` para que la tome (`docker compose -f deploy/docker-compose.yml up -d web`).

Verificar:

```bash
ls -l deploy/.env          # -rw------- ...
git check-ignore deploy/.env   # imprime deploy/.env (si hay .git): está ignorado
```

**Redirección HTTPS y HSTS.** `DJANGO_SSL_REDIRECT`, `DJANGO_HSTS_SECONDS` y `DJANGO_HSTS_PRELOAD` quedan en `0` en la VPS de pruebas: Caddy ya redirige HTTP a HTTPS, y con `DJANGO_SSL_REDIRECT=1` Django redirigiría también el healthcheck interno por HTTP. Activarlos solo si el proxy sanea `X-Forwarded-Proto` (`DJANGO_BEHIND_PROXY=1`) y ya se probó el dominio con HTTPS.

**Guardá una copia de `deploy/.env` fuera de la VPS** (si se pierde `MP_TOKEN_ENCRYPTION_KEY`, los tokens guardados quedan inservibles). Nunca lo commitees.

## 4. Levantar

```bash
cd /opt/ticketjuy
docker compose -f deploy/docker-compose.yml up -d --build
```

La primera vez construye la imagen (varios minutos). Revisar:

```bash
docker compose -f deploy/docker-compose.yml ps
```

Esperado: `ticketjuy-db-1` en `healthy` y `ticketjuy-web-1` en `Up` (`healthy` pasados ~30 s). Si `web` reinicia, mirá `docker compose -f deploy/docker-compose.yml logs web`.

Memoria **después** de levantar:

```bash
docker stats --no-stream
free -m
```

Esperado: cada contenedor por debajo de su límite (256 MiB). Si alguno está pegado al límite, reportalo.

## 5. Superusuario y datos de prueba

```bash
docker compose -f deploy/docker-compose.yml exec web python manage.py createsuperuser
docker compose -f deploy/docker-compose.yml exec web python manage.py seed_infinity
```

El primero pide usuario, email y contraseña. El segundo carga los datos de Infinity (boliche, fechas, etc.); es seguro repetirlo si ya existen.

## 6. Caddy

Agregar el bloque de `deploy/Caddyfile.snippet` **al final** del Caddyfile del sistema (normalmente `/etc/caddy/Caddyfile`), sin modificar lo existente:

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-ticketjuy
cat /opt/ticketjuy/deploy/Caddyfile.snippet | sudo tee -a /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
```

Esperado: `Valid configuration`. Solo si valida:

```bash
sudo caddy reload --config /etc/caddy/Caddyfile
```

(Nunca `systemctl restart caddy`.) Si la validación falla, restaurar la copia: `sudo cp /etc/caddy/Caddyfile.bak-ticketjuy /etc/caddy/Caddyfile` y no recargar. Caddy tarda unos segundos en obtener el certificado la primera vez.

## 7. Verificación

```bash
curl -i https://api.66-97-35-67.sslip.io/api/v1/venues/
```

Esperado: `HTTP/2 200` y un cuerpo JSON con los boliches. Comprobar un estático del admin (la ruta con hash cambia con cada build; sacarla del manifiesto):

```bash
P=$(docker compose -f deploy/docker-compose.yml exec -T web python -c "import json;print(json.load(open('/app/staticfiles/staticfiles.json'))['paths']['admin/css/base.css'])")
curl -s -o /dev/null -w '%{http_code}\n' "https://api.66-97-35-67.sslip.io/static/$P"   # 200
```

`GET /admin/login/` tiene que responder 200 (`managed_venues` ya valida `request.user.is_authenticated`). Abrir `https://api.66-97-35-67.sslip.io/admin/` en el navegador: tiene que cargar el login **con estilos** (no una página sin formato) y permitir entrar con el superusuario.

## 8. Cron

```bash
chmod +x /opt/ticketjuy/deploy/backup.sh
crontab -e
```

Agregar:

```
# Backup diario de la base a las 03:30
30 3 * * * /opt/ticketjuy/deploy/backup.sh >> /opt/ticketjuy/backup.log 2>&1
# Renovar tokens de Mercado Pago a las 04:00
0 4 * * * docker compose -f /opt/ticketjuy/deploy/docker-compose.yml exec -T web python manage.py refresh_mp_tokens >> /opt/ticketjuy/refresh_mp.log 2>&1
# Reintentar los mails de entradas que no salieron, cada 10 minutos
*/10 * * * * docker compose -f /opt/ticketjuy/deploy/docker-compose.yml exec -T web python manage.py send_ticket_emails >> /opt/ticketjuy/ticket_emails.log 2>&1
```

**Mail de entradas.** Cargar las variables `DJANGO_EMAIL_*` y `DJANGO_DEFAULT_FROM_EMAIL` en `deploy/.env` con el SMTP del proveedor y recrear `web`. El remitente tiene que ser de un dominio verificado en el proveedor (SPF y DKIM). Para probar: comprar una entrada y revisar que llegue el mail; si no llega, `docker compose -f deploy/docker-compose.yml logs web | grep 'mail de entradas'` muestra el error. Para reenviar a un comprador: admin de Django → Pedidos → "Reenviar las entradas por email".

Probar a mano una vez: `/opt/ticketjuy/deploy/backup.sh` debe imprimir `Backup OK: /opt/ticketjuy/backups/ticketjuy-<fecha>.sql.gz`. Se conservan 7 días de backups. Restaurar uno:

```bash
gunzip -c /opt/ticketjuy/backups/<archivo>.sql.gz | docker compose -f /opt/ticketjuy/deploy/docker-compose.yml exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

(En una base con datos, restaurar sobre una base vacía: `down -v` y levantar de nuevo antes.)

## Cuenta de Mercado Pago de prueba sin OAuth

Para probar el circuito de pago sin pasar por la autorización, se puede cargar directo el access token de una cuenta vendedora de prueba de Mercado Pago como la cuenta del boliche. El token se lee de la entrada estándar (así no queda en el historial ni en la lista de procesos) desde un archivo, sin imprimirlo:

```bash
docker compose -f deploy/docker-compose.yml exec -T web python manage.py mp_set_test_account infinity < /ruta/al/archivo-con-el-token
```

- Solo sirve con `MP_TEST_TOKENS=1` (y `MP_TOKEN_ENCRYPTION_KEY` configurada).
- Solo acepta cuentas de prueba (las que Mercado Pago marca con `test_user`); una cuenta real se rechaza y no se guarda nada.
- No reemplaza la prueba de la conexión por OAuth: ese flujo se sigue probando aparte.

## Cuenta de cobro directo (prueba o producción)

Para que toda la venta de un boliche entre a una cuenta de Mercado Pago cargada a mano (sin `marketplace_fee` ni reparto), cargá su access token. Se lee de un archivo por entrada estándar y nunca se imprime:

```bash
# cuenta de prueba
docker compose -f deploy/docker-compose.yml exec -T web python manage.py mp_set_own_account infinity < /ruta/archivo-con-el-token
# cuenta real
docker compose -f deploy/docker-compose.yml exec -T web python manage.py mp_set_own_account infinity --produccion < /ruta/archivo-con-el-token
```

- El Access Token sale de la sección Credenciales de la aplicación en el panel de Mercado Pago (pestaña Prueba o Producción).
- Una cuenta real exige `--produccion`; con `--produccion` se rechaza una cuenta de prueba.
- Solo necesita `MP_TOKEN_ENCRYPTION_KEY` (no `MP_TEST_TOKENS`).
- Conectar el boliche por OAuth después reemplaza este modo.

## 9. Actualizar y volver atrás

Antes de actualizar, hacer un backup: `/opt/ticketjuy/deploy/backup.sh`.

Con git (opción A):

```bash
cd /opt/ticketjuy
git fetch origin && git checkout <rama-o-commit> && git pull --ff-only 2>/dev/null || true
docker compose -f deploy/docker-compose.yml up -d --build
```

Con tarball (opción B): extraer el nuevo `git archive` (mismo `tar -xzf ... --strip-components=1`) sobre `/opt/ticketjuy` (sin borrar `deploy/.env` ni `backups/`) y correr el mismo `up -d --build`.

Las migraciones corren solas al arrancar `web`. Revisar `docker compose -f deploy/docker-compose.yml ps` y el `curl` del paso 7.

**Volver atrás**: volver al commit anterior (`git checkout <commit-anterior>` o extraer el tarball viejo) y repetir `up -d --build`. Qué se pierde y qué no:

- El código y la imagen vuelven a la versión anterior. Los datos (volumen `pgdata`) y `deploy/.env` **no se tocan**.
- Si la versión nueva aplicó migraciones, el esquema queda adelantado respecto del código viejo. Si el código viejo no funciona con él, restaurar el backup previo (ver paso 8): **se pierden los datos cargados después de ese backup**.

## 10. Desinstalar todo

```bash
cd /opt/ticketjuy
docker compose -f deploy/docker-compose.yml down -v --rmi local   # contenedores, red, volumen de datos (BORRA la base) e imagen
```

Quitar las dos líneas de `crontab -e`. Quitar el bloque `api.66-97-35-67.sslip.io { ... }` del Caddyfile (o restaurar la copia `.bak-ticketjuy` si no hubo otros cambios), validar y recargar:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo caddy reload --config /etc/caddy/Caddyfile
```

Por último:

```bash
rm -rf /opt/ticketjuy      # incluye backups y .env: copiá antes lo que quieras conservar
sudo rm -f /etc/caddy/Caddyfile.bak-ticketjuy
docker ps -a --filter name=ticketjuy; docker volume ls --filter name=ticketjuy   # ambos deben salir vacíos
```
