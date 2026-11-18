# Ticketera Core

Backend de TicketJuy para operar varios boliches desde una misma plataforma. Moon es el primer cliente. El frontend y el alcance del producto viven en [TicketJuy](https://github.com/ignaMartin22/TicketJuy).

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

Abrí `http://127.0.0.1:8000/admin/` para crear un boliche, un evento y sus tipos de entrada. Activá el boliche y publicá el evento para verlo por API. Para delegar el catálogo, creá un usuario con estado de personal y una membresía de administración en ese boliche. Las membresías de puerta y caja quedan registradas para sus futuras herramientas.

La conexión usa `localhost:5432` por defecto. Configurá `POSTGRES_HOST`, `POSTGRES_PORT` y `POSTGRES_SSLMODE` según la instancia. Fuera de desarrollo, configurá también `DJANGO_SECRET_KEY` y `DJANGO_ALLOWED_HOSTS`.

## API pública

| GET | Respuesta |
| --- | --- |
| `/api/v1/venues/` | `{ venues }` con los boliches activos |
| `/api/v1/venues/<boliche>/events/` | `{ venue, events }` con los eventos publicados y sus tipos de entrada activos |
| `/api/v1/venues/<boliche>/events/<evento>/` | `{ venue, event }` para una fecha concreta |

Los identificadores públicos son slugs. El slug de un evento se resuelve dentro de su boliche. Los precios se guardan como decimales y se exponen en pesos para las pantallas de catálogo. Cada tipo de entrada expone `available`, que descuenta las entradas vendidas y las reservas vigentes. `sold` conserva la cantidad vendida.

## Pedidos sin cuenta

`POST /api/v1/venues/<boliche>/events/<evento>/orders/` recibe JSON y una cabecera `Idempotency-Key` con un UUID generado para el intento. Se reutiliza la misma clave al reintentar después de un error de red.

```json
{
  "email": "comprador@example.com",
  "items": [{ "ticketTypeId": "general", "quantity": 2 }]
}
```

La respuesta `201` incluye `order.id`, `status`, `expiresAt` y los importes en ARS como cadenas decimales. El backend guarda los precios y el cargo de servicio vigentes en el momento de reservar. El máximo es 10 entradas por pedido. Un reintento con el mismo contenido devuelve `200` con el pedido original. Una clave reutilizada con otro contenido o el cupo insuficiente devuelven `409`.

`GET /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/` devuelve el estado `pending_payment` o `expired`. El pedido reserva cupo durante 10 minutos. Al vencimiento, ese cupo vuelve a estar disponible en las consultas y en nuevas compras sin una tarea periódica. Conservá el UUID como dato privado del comprador. La respuesta omite su email.

La confirmación de pagos, la emisión y el manejo de pagos tardíos forman parte de la siguiente integración. Antes de habilitar la compra pública, agregá protección contra reservas automatizadas en el punto de entrada.

Ejecutá las pruebas con `.venv/Scripts/python.exe manage.py test catalog`. Usá un usuario PostgreSQL de desarrollo con permiso `CREATEDB` para que Django pueda crear y eliminar su base de pruebas.

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

El aislamiento de datos por boliche aplica desde la primera entrega. El piloto usa Moon, y las pruebas deben incluir un segundo boliche para detectar accesos cruzados.
