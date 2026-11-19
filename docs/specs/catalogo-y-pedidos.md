# Catálogo y pedidos

API pública que consume TicketJuy desde su servidor. El navegador del comprador no habla directo con el core.

## Catálogo

| GET | Respuesta |
| --- | --- |
| `/api/v1/venues/` | `{ venues }`: boliches activos |
| `/api/v1/venues/<boliche>/events/` | `{ venue, events }`: fechas publicadas con sus tipos de entrada activos |
| `/api/v1/venues/<boliche>/events/<evento>/` | `{ venue, event }`: una fecha |

- **Debe:** solo se exponen boliches activos, fechas publicadas y tipos de entrada activos. Cualquier otra cosa da 404.
- Campos vacíos del boliche (`tagline`, `logo`, `cover`, `flyerBackdrop`, `mapsUrl`) llegan como `null`. `dressCode` también.
- `lineup` es una lista: un artista por línea del campo del admin.
- Cada tipo de entrada trae `price` (número en pesos), `capacity`, `sold` y `available`.
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
| `invalid_phone` | Teléfono que no es E.164 (`+` y de 8 a 15 dígitos). Los celulares argentinos van como `+549` y 10 dígitos |
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
| 409 `ticket_type_unavailable` | Un tipo de entrada no existe en esa fecha o está inactivo |
| 409 `insufficient_capacity` | No alcanza el cupo |

## Consulta de un pedido

`GET /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/`

- `status`: `pending_payment` mientras la reserva está vigente, `expired` después.
- Importes en ARS como cadenas decimales (`"233.00"`): `total`, y por línea `unitPrice` y `unitServiceFee`.
- **Debe:** la respuesta no incluye el email, el nombre ni el teléfono del comprador. El UUID funciona como dato privado del comprador.
- **Debe:** un pedido solo se encuentra con su boliche y su fecha. Con otra ruta da 404.

## Admin del catálogo

- **Debe:** el cupo de un tipo de entrada no puede bajar de `vendidas + reservas vigentes`. La validación bloquea la misma fila que la reserva, así que no compite con una compra en curso.
- `sold` no se edita a mano.

## Pendiente

- Confirmar el pago y pasar de reserva a vendida: sumar a `sold` y dejar de contar ese pedido como reserva vigente. Si no, el cupo se cuenta dos veces hasta que vence la reserva.
- Qué hacer si el pago llega después del vencimiento: emitir si queda cupo o reembolsar.
- Protección contra reservas automatizadas (captcha y límite por IP).
