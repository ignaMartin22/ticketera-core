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

Para tener datos de prueba, `.venv/Scripts/python.exe manage.py seed_infinity` crea el boliche `infinity` con las mismas fechas que la demo de TicketJuy.

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

## Specs

El comportamiento de cada parte del core está en [docs/specs](docs/specs/README.md): [clientes](docs/specs/clientes.md), [multi-boliche](docs/specs/multi-boliche.md), [catálogo y pedidos](docs/specs/catalogo-y-pedidos.md), [ABM de eventos del boliche](docs/specs/eventos-del-boliche.md), [comisiones](docs/specs/comisiones.md), [panel de la plataforma](docs/specs/panel-plataforma.md) y [conexiones](docs/specs/conexiones.md). Si un cambio altera un comportamiento, actualizá la spec en el mismo PR.

La conexión local usa `localhost:5432` por defecto. Configurá `POSTGRES_HOST`, `POSTGRES_PORT` y `POSTGRES_SSLMODE` según la instancia, o `DATABASE_URL` para una connection string completa (Neon). Fuera de desarrollo, configurá también `DJANGO_SECRET_KEY` y `DJANGO_ALLOWED_HOSTS`.

## API pública

| GET | Respuesta |
| --- | --- |
| `/api/v1/venues/` | `{ venues }` con los boliches activos |
| `/api/v1/venues/<boliche>/events/` | `{ venue, events }` con los eventos publicados y sus tipos de entrada activos |
| `/api/v1/venues/<boliche>/events/<evento>/` | `{ venue, event }` para una fecha concreta |

Los identificadores públicos son slugs. El slug de un evento se resuelve dentro de su boliche. Los precios se guardan como decimales y se exponen en pesos para las pantallas de catálogo. Cada tipo de entrada expone `available`, que descuenta las entradas vendidas y las reservas vigentes. `sold` conserva la cantidad vendida.

Además, `/api/v1/manage/` es la API de gestión para el dashboard del dueño de cada boliche: alta, edición, pausa y baja de fechas y entradas. Se autentica con `Authorization: Bearer <clave>` (una clave por usuario, generada con `manage.py create_api_token <email>`) limitada a los boliches que el usuario administra. Ver la [spec del ABM de eventos](docs/specs/eventos-del-boliche.md).

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

La [spec de catálogo y pedidos](docs/specs/catalogo-y-pedidos.md) tiene todas las validaciones y los códigos de error. `name` es el nombre y apellido del titular. `phone` es el WhatsApp en formato E.164 (`+549` y los 10 dígitos para celulares argentinos). La respuesta `201` incluye `order.id`, `status`, `expiresAt` y los importes en ARS como cadenas decimales. El backend guarda los precios, el cargo de servicio y la comisión de la plataforma vigentes en el momento de reservar. El máximo es 10 entradas por pedido. Un reintento con el mismo contenido devuelve `200` con el pedido original. Una clave reutilizada con otro contenido o el cupo insuficiente devuelven `409`.

`GET /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/` devuelve el estado `pending_payment` o `expired`. El pedido reserva cupo durante 10 minutos. Al vencimiento, ese cupo vuelve a estar disponible en las consultas y en nuevas compras sin una tarea periódica. Conservá el UUID como dato privado del comprador. La respuesta omite su email, nombre y teléfono.

La confirmación de pagos, la emisión y el manejo de pagos tardíos forman parte de la siguiente integración. Antes de habilitar la compra pública, agregá protección contra reservas automatizadas en el punto de entrada.

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

Los pasos 1 y 2 están hechos, junto con el panel de la plataforma para administrar los boliches. Lo que falta para el piloto con Infinity está en [pendientes](docs/pendientes.md).

El aislamiento de datos por boliche aplica desde la primera entrega. El piloto usa Infinity, y las pruebas deben incluir un segundo boliche para detectar accesos cruzados.
