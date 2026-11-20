# Ticketera Core

Backend compartido para operar varios boliches desde una misma plataforma. Cada boliche cliente tiene su propio repo de frontend, con su sitio público y el dashboard del dueño, que se conecta a esta API. Infinity (ex Moon) es el primer cliente y su repo es [TicketJuy](https://github.com/ignaMartin22/TicketJuy). La administración del core la usa solo el equipo de la plataforma: ver [Clientes](docs/specs/clientes.md).

## Ejecutar el catálogo localmente

Creá una base PostgreSQL y un usuario con acceso a ella. Con Python 3.12 o superior, configurá los datos de conexión desde PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:DJANGO_DEBUG = "1"
$env:POSTGRES_DB = "ticketera"
$env:POSTGRES_USER = "ticketera"
$env:POSTGRES_PASSWORD = "<contraseña de desarrollo>"
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py createsuperuser
.venv/Scripts/python.exe manage.py runserver
```

Para tener datos de prueba, `.venv/Scripts/python.exe manage.py seed_infinity` crea el boliche `infinity` con las mismas fechas que la demo de TicketJuy. Para que el personal de puerta entre a la API (`/api/v1/auth/`), cargá a cada persona en el equipo del boliche desde `/plataforma/` con rol `door`; la membresía de caja queda registrada para su futura herramienta. Los miembros de los boliches no reciben `is_staff` ni entran a `/admin/`.

En cualquier shell podés definir las variables en `.env` (copiá `.env.example`): `python-dotenv` las carga solas y las variables reales del entorno tienen prioridad.

## Base de datos en Neon

1. Creá el proyecto en [Neon](https://neon.tech) y copiá la connection string desde **Connect** del branch (incluye `sslmode=require`).
2. Definí `DATABASE_URL` en `.env` con esa string. Mientras `DATABASE_URL` esté presente se ignoran las variables `POSTGRES_*`.
3. Para `migrate`, `seed_infinity` y `createsuperuser` usá el endpoint **directo** (sin `-pooler`): el pooler es PgBouncer en modo transacción y las migraciones pueden fallar ahí. La app sí puede usar el endpoint con `-pooler`.
4. La base de tests la crea Django al vuelo (`test_<nombre>`); funciona en Neon pero es lenta. Corré los tests contra la base local.

Hay dos paneles:

- `http://127.0.0.1:8000/plataforma/`: el [panel de la plataforma](docs/specs/panel-plataforma.md), solo para superusuarios. Ahí se dan de alta los boliches, se activan o pausan, se definen sus comisiones, se da acceso a su equipo y se ven sus conexiones.
- `http://127.0.0.1:8000/admin/`: el admin de Django, para cargar eventos y tipos de entrada mientras el dashboard de cada cliente no esté conectado a la API de gestión. Publicá el evento para verlo por API.

Los dos son solo para superusuarios. La gente de cada boliche no entra al core.

Las membresías de puerta y caja quedan registradas para sus futuras herramientas.

## Producción

El servidor es `gunicorn` y los estáticos los sirve `whitenoise` (con hash en el nombre, así que `collectstatic` es requisito del deploy):

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn ticketera.wsgi
```

Variables necesarias: `DATABASE_URL`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` (con esquema, ej. `https://midominio.com`) y `DJANGO_BEHIND_PROXY=1` detrás del proxy HTTPS. `DJANGO_SSL_REDIRECT`, `DJANGO_HSTS_SECONDS` y `DJANGO_HSTS_PRELOAD` son opt-in: ver `.env.example`.

## Specs

El comportamiento de cada parte del core está en [docs/specs](docs/specs/README.md): [clientes](docs/specs/clientes.md), [multi-boliche](docs/specs/multi-boliche.md), [catálogo y pedidos](docs/specs/catalogo-y-pedidos.md), [ABM de eventos del boliche](docs/specs/eventos-del-boliche.md), [comisiones](docs/specs/comisiones.md), [panel de la plataforma](docs/specs/panel-plataforma.md) y [conexiones](docs/specs/conexiones.md). Si un cambio altera un comportamiento, actualizá la spec en el mismo PR.

La conexión local usa `localhost:5432` por defecto. Configurá `POSTGRES_HOST`, `POSTGRES_PORT` y `POSTGRES_SSLMODE` según la instancia, o `DATABASE_URL` para una connection string completa (Neon). Fuera de desarrollo, configurá también `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS`.

## API pública

| GET | Respuesta |
| --- | --- |
| `/api/v1/venues/` | `{ venues }` con los boliches activos |
| `/api/v1/venues/<boliche>/events/` | `{ venue, events }` con los eventos publicados y sus tipos de entrada activos |
| `/api/v1/venues/<boliche>/events/<evento>/` | `{ venue, event }` para una fecha concreta |

Los identificadores públicos son slugs. El slug de un evento se resuelve dentro de su boliche. Los precios se guardan como decimales y se exponen en pesos para las pantallas de catálogo. Cada tipo de entrada expone `available`, que descuenta las entradas vendidas y las reservas vigentes. `sold` conserva la cantidad vendida.

Además, `/api/v1/manage/` es la API de gestión para el dashboard del dueño de cada boliche: alta, edición, pausa y baja de fechas y entradas. Se autentica con `Authorization: Bearer <token>`: sirve el token de `POST /api/v1/auth/login/` (12 horas) o una clave de larga duración por usuario generada con `manage.py create_api_token <email>`. Queda limitada a los boliches que el usuario administra. Las rutas de la conexión de Mercado Pago viven bajo el mismo prefijo. Ver la [spec del ABM de eventos](docs/specs/eventos-del-boliche.md) y la [guía del front](docs/integracion-front.md).

## Pedidos sin cuenta

`POST /api/v1/venues/<boliche>/events/<evento>/orders/` recibe JSON y una cabecera `Idempotency-Key` con un UUID generado para el intento. Se reutiliza la misma clave al reintentar después de un error de red.

```json
{
  "email": "comprador@example.com",
  "name": "Juan Pérez",
  "phone": "+5493884123456",
  "items": [{ "ticketTypeId": "general", "quantity": 2 }]
}
```

La [spec de catálogo y pedidos](docs/specs/catalogo-y-pedidos.md) tiene todas las validaciones y los códigos de error. `name` es el nombre y apellido del titular. `phone` es el WhatsApp en formato E.164 (`+549` y los 10 dígitos para celulares argentinos); es opcional mientras las entradas no se envíen por WhatsApp. La respuesta `201` incluye `order.id`, `status`, `expiresAt` y los importes en ARS como cadenas decimales. El backend guarda los precios, el cargo de servicio y la comisión de la plataforma vigentes en el momento de reservar. El máximo es 10 entradas por pedido. Un reintento con el mismo contenido devuelve `200` con el pedido original. Una clave reutilizada con otro contenido o el cupo insuficiente devuelven `409`.

`GET /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/` devuelve el estado `pending_payment` o `expired`. El pedido reserva cupo durante 10 minutos. Al vencimiento, ese cupo vuelve a estar disponible en las consultas y en nuevas compras sin una tarea periódica. Conservá el UUID como dato privado del comprador. La respuesta omite su email, nombre y teléfono.

## Entradas y QR

Cada unidad de un pedido pagado es una entrada con su propio QR. Un pedido con total $0 emite sus entradas al reservar. Un pedido con precio las emite cuando el equipo de la plataforma lo selecciona en el `/admin/` de Django (`Pedidos`) y aplica "Marcar como pagado y emitir entradas". La confirmación del pago de Mercado Pago llama a la misma función, `catalog.tickets.issue_tickets`.

El pedido pasa al estado `paid` y su respuesta incluye `tickets`, una lista vacía mientras no esté pagado:

```json
{ "id": "<uuid>", "ticketTypeId": "general", "ticketTypeName": "General", "qr": "<texto del QR>", "usedAt": null }
```

`qr` es el texto que el frontend dibuja como código QR: el id de la entrada más una firma hecha con `DJANGO_SECRET_KEY`. Cambiar esa clave invalida los QR ya emitidos.

## Mercado Pago: conexión del boliche

Cada boliche conecta su propia cuenta de Mercado Pago por OAuth. El administrador toca "conectar", autoriza en Mercado Pago y la plataforma guarda los tokens cifrados y los renueva sola. El estado de la conexión se refleja en `VenueIntegration` (`connected`, `expired`, `error` o `disconnected`), que es lo que muestra el panel y nunca contiene tokens.

| Variable | Uso |
| --- | --- |
| `MP_APP_ID` | Id de la aplicación de Mercado Pago |
| `MP_CLIENT_SECRET` | Client secret de la aplicación |
| `MP_REDIRECT_URI` | URL pública del callback: `https://<dominio>/api/v1/mercadopago/oauth/callback/` |
| `MP_TOKEN_ENCRYPTION_KEY` | Clave Fernet para cifrar los tokens (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) |
| `MP_CONNECT_RETURN_URL` | A dónde vuelve el navegador al terminar (por defecto `/`) |
| `MP_TEST_TOKENS` | `1` agrega `test_token=true` al intercambio, para credenciales de prueba |

Si falta alguna de las cuatro primeras, la app arranca igual y las rutas de conexión responden `503` (`mp_not_configured`). Perder `MP_TOKEN_ENCRYPTION_KEY` obliga a reconectar todos los boliches.

| Ruta | Uso |
| --- | --- |
| `POST /api/v1/manage/venues/<boliche>/mercadopago/connect/` | Rol `admin`. Devuelve `{ authorizationUrl }`; el `state` firmado vence a los 10 minutos |
| `GET /api/v1/mercadopago/oauth/callback/` | Lo abre el navegador al volver de Mercado Pago. Siempre redirige a `MP_CONNECT_RETURN_URL` con `mp=connected&venue=<slug>` o `mp=error&reason=<código>` (`invalid_state`, `denied`, `exchange_failed`, `not_configured`) |
| `GET /api/v1/manage/venues/<boliche>/mercadopago/` | Rol `admin`. `{ connected, needsReconnect, mpUserId, liveMode, connectedAt, expiresAt }`, sin tokens |
| `DELETE /api/v1/manage/venues/<boliche>/mercadopago/` | Rol `admin`. Borra la cuenta guardada (`204`) |

`catalog.mp_connect.get_access_token(venue)` devuelve el access token en claro mientras no haya vencido y lo renueva si le quedan menos de 30 días. Un fallo de red, 429 o 5xx al renovar no desconecta al boliche; un rechazo de Mercado Pago (400/401) marca `needsReconnect`, que es un aviso para el panel y no corta el cobro mientras el token siga vigente. Levanta `MercadoPagoNotConnected` si no hay cuenta o el token ya venció sin poder renovarse. Programá `manage.py refresh_mp_tokens` una vez por día para renovar las cuentas por vencer aunque nadie cobre.

## Personal

Todo el personal de los boliches entra con el mismo login. El token es el mismo que usa la portería y el rol de la persona en cada boliche decide qué puede hacer.

| Ruta | Uso |
| --- | --- |
| `POST /api/v1/auth/login/` | Recibe `{ username, password }` y devuelve `{ token, expiresIn, user, memberships }`. Sirve cualquier rol (`admin`, `door`, `cashier`) en un boliche activo; sin membresías responde `403` (`no_membership`), también para superusuarios. |
| `GET /api/v1/auth/me/` | Con `Authorization: Bearer <token>` devuelve `{ user, memberships }` leídos de la base en el momento. Sin token válido, `401`. |

`memberships` es una lista de `{ venue: { slug, name }, role }` solo de boliches activos, ordenada por slug. Los logins `auth` y `door` comparten el límite de intentos por usuario. Las rutas por boliche usan el decorador `venue_staff_view(*roles)` de `catalog/staff_auth.py`: `401` sin token y `404` si el boliche no existe, está inactivo o la persona no tiene uno de esos roles en él.

## Portería

El personal de puerta entra con su usuario de Django. Necesita una membresía con rol `door` o `admin` en el boliche.

| Ruta | Uso |
| --- | --- |
| `POST /api/v1/door/login/` | Recibe `{ username, password }` y devuelve `{ token, expiresIn, venues }`. El token dura 12 horas. |
| `GET /api/v1/door/venues/<boliche>/events/` | Eventos publicados del boliche, para elegir la fecha de la noche. |
| `POST /api/v1/door/venues/<boliche>/events/<evento>/scan/` | Recibe `{ token }` con el texto del QR y quema la entrada. |
| `GET /api/v1/door/venues/<boliche>/events/<evento>/attendance/` | Entradas emitidas e ingresadas, por tipo y una por una. |

El login limita los intentos por usuario: con 10 fallos en 15 minutos responde `429` (`too_many_attempts`, con `retryAfter` y la cabecera `Retry-After`) sin evaluar la contraseña; un login correcto borra los fallos.

Todas las rutas salvo el login llevan la cabecera `Authorization: Bearer <token>`. Sin token válido responden `401`; con un usuario sin rol en ese boliche, `404`.

El escaneo responde según el campo `result`:

| `result` | Estado | Significado |
| --- | --- | --- |
| `admitted` | 200 | Ingreso válido. La entrada queda quemada con la hora y el usuario que la escaneó. |
| `already_used` | 409 | Ya se había escaneado. Incluye la hora y el usuario del primer escaneo. |
| `wrong_event` | 409 | Es de otra fecha del mismo boliche. No se quema e incluye el evento correcto. |
| `invalid` | 404 | QR falso, alterado o de otro boliche. |

El escaneo funciona solo con conexión: decide el servidor, así que dos celulares no pueden aceptar el mismo QR. El admin de Django lista las entradas por evento con el filtro de ingreso.

El manejo de pagos tardíos forma parte de la integración con Mercado Pago. Antes de habilitar la compra pública, agregá protección contra reservas automatizadas en el punto de entrada.

Ejecutá las pruebas con `.venv/Scripts/python.exe manage.py test catalog backoffice`. Usá un usuario PostgreSQL de desarrollo con permiso `CREATEDB` para que Django pueda crear y eliminar su base de pruebas.

## Flujos de Fuego Austral que sirven de referencia

Los archivos seleccionados están disponibles en [la referencia local](reference/fuego-austral/README.md).

| Flujo | Referencia | Adaptación para TicketJuy |
| --- | --- | --- |
| Eventos y cupos | [Eventos](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/events/models.py) y [tipos de entrada](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/models.py) | Asociar cada evento a un boliche. Controlar cupos por tipo y reservarlos en una transacción. |
| Compra y emisión | [Checkout](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/views/checkout.py) y [emisión de entradas](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/processing.py) | Permitir compras sin cuenta y emitir una entrada por unidad pagada. |
| Pagos | [Webhook de Mercado Pago](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/views/webhooks.py) y [conciliación](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/payment_check_cron.py) | Vincular la cuenta del boliche por OAuth. Crear el pago con el token del vendedor y guardar la comisión de la plataforma por orden. Confirmar cada pago una sola vez. |
| Caja y puerta | [Flujo de caja](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/caja/services/sales.py) | En caja, registrar canal y operador y usar el mismo stock que la venta online. En puerta, validar un QR firmado y registrar el ingreso. |

## Orden de implementación

1. Boliches, membresías del personal y catálogo de eventos y tipos de entrada. El frontend puede reemplazar sus datos de prueba una vez disponible el catálogo.
2. Órdenes de invitados y reservas de cupo con vencimiento.
3. Checkout Pro con split por boliche, confirmación idempotente y emisión de entradas.
4. Escaneo en puerta con sincronización offline y panel de ventas y asistencia. Si dos dispositivos están desconectados, pueden aceptar el mismo QR antes de sincronizar. El piloto necesita una regla de operación para resolver ese caso.
5. Caja, RRPP, listas y mesas VIP después del MVP.

Los pasos 1 y 2 están hechos, junto con el panel de la plataforma para administrar los boliches. Del paso 3 están Mercado Pago (OAuth del boliche, link de pago, webhook y conciliación) y la emisión de entradas con QR, y del paso 4 el escaneo en puerta con conexión y la asistencia; faltan el modo offline y el panel de ventas. Lo que falta para el piloto con Infinity está en [pendientes](docs/pendientes.md).

El aislamiento de datos por boliche aplica desde la primera entrega. El piloto usa Infinity, y las pruebas deben incluir un segundo boliche para detectar accesos cruzados.

## Mercado Pago: pago del pedido

El front le pide al backend el link de pago de un pedido con precio y redirige al comprador. El backend crea una order de Checkout Pro (API de Orders, `POST /v1/orders`) con el token del boliche y la comisión de la plataforma (`marketplace_fee`, la suma de cargo de servicio y comisión de cada ítem con los importes guardados al reservar). La confirmación del pago por webhook se describe más abajo.

| Variable | Uso |
| --- | --- |
| `FRONT_BASE_URL` | URL pública del front; `success_url`, `failure_url` y `pending_url` son `<FRONT_BASE_URL>/<boliche>/<evento>/pedido/<id>` |

`POST /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/payment/` es público (quien tiene el uuid del pedido puede pagar) y no lleva cuerpo. Devuelve `200 { initPoint }`. Si el pedido ya tiene link y sigue vigente devuelve el mismo, sin llamar de nuevo a Mercado Pago.

| Código | Cuándo |
| --- | --- |
| `404 not_found` | El pedido no existe o no es de ese boliche y evento |
| `409 order_already_paid` | El pedido ya está pagado (incluye los pedidos gratis) |
| `409 order_expired` | La reserva venció |
| `409 venue_not_connected` | El boliche no tiene una cuenta de Mercado Pago utilizable |
| `502 mp_unavailable` | Mercado Pago falló al crear la order, o devolvió una sin `id`, sin `checkout_url` o con otro `total_amount`; no se guarda nada |
| `503 mp_not_configured` | Falta `FRONT_BASE_URL` o la configuración de Mercado Pago |

La order se crea con `type: "online"`, `processing_mode: "manual"` y `capture_mode: "automatic"`. Los montos (`total_amount`, `unit_price`, `marketplace_fee`) viajan como texto con 2 decimales. Cada ítem es una línea del pedido, con el precio por unidad que paga el comprador (precio más cargo de servicio); la suma da el total del pedido. Solo se manda el email del comprador, nunca el nombre ni el teléfono. La order vence junto con la reserva (`expiration_time` en minutos enteros, redondeado hacia arriba, mínimo `PT1M`) y excluye los pagos en efectivo (`config.payment_method.not_allowed_types`: `ticket`, `atm`). No se manda `notification_url`: los avisos se configuran en la aplicación de Mercado Pago. La cabecera `X-Idempotency-Key` es fija por pedido, así un reintento tras un corte de red no crea dos orders. La comisión de la plataforma es la suma de cantidad por `redondeo(precio × buyer_fee_rate)` de cada ítem, con los precios guardados en el pedido y en los dos modos de cargo del boliche; se guarda en `Order.platform_fee` junto con `mp_order_id` (el id `ORD...`) y `mp_init_point` (el `checkout_url`). Con tasa 0 la order no lleva `marketplace_fee`.

## Mercado Pago: confirmación del pago

Una order pagada emite las entradas del pedido una sola vez, lleguen los avisos repetidos o por dos caminos a la vez. Antes de emitir, el backend siempre consulta la order a Mercado Pago (`GET /v1/orders/<id>`) con el token del boliche: nunca confía en el contenido del aviso. **No hay reembolsos**: si el pago llega cuando la reserva venció y no queda cupo, no se emiten entradas y el pago queda registrado para que el boliche lo resuelva a mano.

| Variable | Uso |
| --- | --- |
| `MP_WEBHOOK_SECRET` | Clave secreta de la aplicación (Tus integraciones, Webhooks). Sin ella el webhook responde `503 mp_not_configured` |

En el panel de Mercado Pago hay que suscribir el webhook a los eventos "Order (Mercado Pago)" y "Vinculación de aplicaciones" con la URL `<PUBLIC_API_URL>/api/v1/mercadopago/webhook/` (`PUBLIC_API_URL` solo se usa para armar esa URL en el panel; el backend no la necesita para cobrar).

`POST /api/v1/mercadopago/webhook/` (sin Bearer): valida la firma `x-signature` (HMAC-SHA256 sobre `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`, con el `data.id` de la query; para ids alfanuméricos como `ORD...` Mercado Pago firma el id en minúsculas, y la consulta usa el id tal cual llegó) y responde `200 { ok: true }`, sin datos del pedido ni del comprador.

| Respuesta | Cuándo |
| --- | --- |
| `200` | Aviso procesado, o ignorado (otro tipo de aviso, o order de una cuenta que no conocemos) |
| `400 invalid_request` | Cuerpo que no es un objeto JSON, o aviso de order sin `data.id` o `user_id` |
| `401 invalid_signature` | Firma ausente, mal formada o distinta; no se consulta nada |
| `500 mp_unavailable` | Mercado Pago falló o el boliche no tiene token utilizable: Mercado Pago reintenta |
| `503 mp_not_configured` | Falta `MP_WEBHOOK_SECRET` |

- `type: "order"`: busca las cuentas con `mp_user_id` igual al `user_id` del aviso (un dueño puede tener varios boliches), consulta la order con el token de cada una y confirma el pedido en la primera que lo reconoce. Una order solo confirma pedidos del boliche dueño de la cuenta que la cobró.
- `type: "payment"`: se responde `200` sin hacer nada (ya no se usa).
- `type: "mp-connect"` con `action: "application.deauthorized"`: marca `needs_reconnect` y vence el token de todas las cuentas de ese `user_id`.

Cada order consultada queda en `Payment` (`mp_order_id`, y `mp_reference` con la referencia del primer pago para buscarlo en el panel de Mercado Pago) con su `outcome`:

| `outcome` | Significado |
| --- | --- |
| `issued` | Aprobado, entradas emitidas |
| `no_capacity` | Aprobado, pero la reserva venció y no hay cupo. Pagado sin entradas, a revisar |
| `mismatch` | Aprobado, pero el monto o la moneda no coinciden con el pedido. Sin entradas, a revisar |
| `duplicate` | Aprobado, pero el pedido ya estaba pagado por otra order. A revisar |
| `not_approved` | Cualquier order que no sea `status: processed` con `status_detail: accredited` |

Un pago `issued` que después llega con otro estado (por ejemplo `refunded`) actualiza su `status` pero conserva `issued`: las entradas no se tocan. La plataforma ve los pagos (solo lectura) en el `/admin/`, con filtro por resultado. Los pedidos con un pago `no_capacity`, `mismatch` o `duplicate` devuelven `paymentReview: true` para que el front avise al comprador que el boliche tiene que revisar su pago.

`POST /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/payment/sync/` es público y sin cuerpo: para cuando el comprador vuelve de Mercado Pago antes de que llegue el aviso. Si el pedido no está pagado y tiene link, consulta la order con su `mp_order_id` y la confirma; si Mercado Pago falla devuelve el pedido como esté. Responde `200 { order }` (o `404 not_found`).

`manage.py reconcile_mp_payments` (cron cada pocos minutos) hace lo mismo con los pedidos sin pagar, con order, de las últimas 48 horas, e imprime cuántos revisó, confirmó y falló.
