# Catálogo y pedidos

API pública que consume el repo de cada cliente (TicketJuy para Infinity) desde su servidor. El navegador del comprador no habla directo con el core.

## Catálogo

| GET | Respuesta |
| --- | --- |
| `/api/v1/venues/` | `{ venues }`: boliches activos |
| `/api/v1/venues/<boliche>/events/` | `{ venue, events }`: fechas publicadas con sus tipos de entrada activos |
| `/api/v1/venues/<boliche>/events/<evento>/` | `{ venue, event }`: una fecha |

- **Debe:** solo se exponen boliches activos, fechas publicadas y tipos de entrada activos. Cualquier otra cosa da 404.
- Campos vacíos del boliche (`tagline`, `logo`, `cover`, `flyerBackdrop`, `mapsUrl`) llegan como `null`. `dressCode` también.
- `lineup` es una lista: un artista por línea del campo del admin.
- La fecha trae `salesOpen`: en `false`, la fecha se sigue mostrando pero no acepta reservas.
- Cada tipo de entrada trae `kind` (`common` o `vip`), `price` (número en pesos), `capacity`, `sold` y `available`.
- **Debe:** `available = capacity − sold − reservas vigentes`, nunca menos de 0.
- El boliche trae `feeMode` y `buyerFeeRate` para calcular el precio final. Ver [Comisiones](comisiones.md).

## Reserva de un pedido

`POST /api/v1/venues/<boliche>/events/<evento>/orders/`

- Cabecera `Content-Type: application/json`. Si no, responde 415 `expected_json`.
- Cabecera `Idempotency-Key` con un UUID por intento de compra. El cliente lo reutiliza al reintentar.
- Cuerpo de hasta 8 KB. Si es más grande, responde 413 `request_too_large`.

```json
{
  "email": "comprador@example.com",
  "name": "Juan Pérez",
  "phone": "+5493884123456",
  "items": [{ "ticketTypeId": "general", "quantity": 2 }]
}
```

### Validación (400)

| Error | Cuándo |
| --- | --- |
| `invalid_request` | Falta el `Idempotency-Key` o no es UUID, el JSON es inválido o no es un objeto, o un `ticketTypeId` no es un slug válido |
| `invalid_email` | Email ausente, de más de 254 caracteres o con formato inválido. Se guarda sin espacios al costado |
| `invalid_name` | Nombre vacío o de más de 150 caracteres. Se guarda con los espacios internos colapsados |
| `invalid_phone` | Teléfono que no es E.164 (`+` y de 8 a 15 dígitos). Los celulares argentinos van como `+549` y 10 dígitos. El teléfono es opcional: sin él (o vacío) el pedido se crea igual |
| `invalid_items` | `items` vacío, con más de 10 líneas, un tipo repetido, o una cantidad que no es un entero entre 1 y 10 |
| `order_limit_exceeded` | Más de 10 entradas en total |

### Reserva

- **Debe:** la reserva bloquea el evento y sus tipos de entrada en la base. Dos pedidos simultáneos no pueden quedarse con las últimas entradas.
- La reserva dura 10 minutos (`expires_at`). Al vencer, el cupo vuelve a estar disponible sin ninguna tarea periódica: las consultas solo cuentan reservas con `expires_at` en el futuro.
- **Debe:** cada línea guarda precio, cargo de servicio y comisión de la plataforma vigentes al reservar.

| Respuesta | Cuándo |
| --- | --- |
| 201 | Pedido nuevo |
| 200 | Reintento con la misma clave y el mismo contenido: devuelve el pedido original, aunque haya vencido |
| 404 `event_unavailable` | El boliche está pausado o la fecha no existe o no está publicada |
| 409 `idempotency_key_reused` | La misma clave con otro comprador o con otro carrito |
| 409 `sales_paused` | La fecha tiene la venta pausada. No afecta el reintento de una reserva ya hecha |
| 409 `ticket_type_unavailable` | Un tipo de entrada no existe en esa fecha o está inactivo |
| 409 `insufficient_capacity` | No alcanza el cupo |

## Consulta de un pedido

`GET /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/`

- `status`: `pending_payment` mientras la reserva está vigente, `expired` después.
- Importes en ARS como cadenas decimales (`"233.00"`): `total`, y por línea `unitPrice` y `unitServiceFee`.
- **Debe:** la respuesta no incluye el email, el nombre ni el teléfono del comprador. El UUID funciona como dato privado del comprador.
- **Debe:** un pedido solo se encuentra con su boliche y su fecha. Con otra ruta da 404.

## Pago y entradas

- `POST .../orders/<uuid>/payment/` crea el link de pago (order de Checkout Pro) con el token del boliche y la comisión de la plataforma. Ver [Comisiones](comisiones.md).
- **Debe:** el webhook de Mercado Pago confirma cada pago una sola vez. Al confirmar, el pedido pasa a `paid`, se emiten las entradas (una por unidad, con QR firmado de un solo uso) y deja de contarse como reserva vigente. Si la reserva venció, se emite solo si queda cupo.
- Los pedidos con importe 0 se marcan pagados y emiten sus entradas al reservar.
- `GET` del pedido devuelve `status: paid`, las entradas con su texto de QR y `paymentReview`.

## Admin del catálogo

- **Debe:** el cupo de un tipo de entrada no puede bajar de `vendidas + reservas vigentes`. La validación bloquea la misma fila que la reserva, así que no compite con una compra en curso.
- `sold` no se edita a mano.

## Pendiente

- Reembolsos. Hoy un pago que llega con la reserva vencida y sin cupo (`no_capacity`), con otro monto (`mismatch`) o repetido (`duplicate`) queda marcado para que la plataforma lo revise.
- Protección contra reservas automatizadas (captcha y límite de intentos).
  - La IP que ve el core (`REMOTE_ADDR`) es la del servidor del repo del cliente, no la del comprador. El límite tiene que usar la IP del comprador, reenviada por el front y válida solo con la credencial del cliente, o claves como el email o el teléfono. Ver [Pendientes](../pendientes.md), ítem 5.
