# Multi-boliche

Un solo core sirve a varios boliches. Cada boliche (`Venue`) es un tenant: todos comparten la base de datos y cada registro pertenece a un boliche.

## Modelo

```
Venue ─┬─ Membership ── User
       ├─ Event ── TicketType ── OrderItem ── Order
       ├─ FeeChange
       └─ VenueIntegration
```

- `Venue.slug` identifica al boliche en la API (`/api/v1/venues/infinity/`) y en la configuración del repo del cliente. Es único y no se cambia después del alta.
- Un `Event` pertenece a un boliche. Su `slug` es único dentro del boliche: dos boliches pueden tener una fecha `fin-de-ano`.
- Un `TicketType` pertenece a un evento. Su `slug` es único dentro del evento.
- Un `Order` pertenece a un evento y, por lo tanto, a un boliche.

## Estado del boliche

- `is_active = false`: el boliche no aparece en la API pública, sus fechas dan 404 y no acepta reservas.
- Un boliche nuevo se crea pausado. Se activa desde el [panel de la plataforma](panel-plataforma.md).
- Una fecha solo es pública si su boliche está activo y la fecha tiene `is_published = true`.

## Niveles de acceso

| Nivel | Quién | Dónde | Qué puede hacer |
| --- | --- | --- | --- |
| Plataforma | Superusuarios de Django | `/plataforma/` y `/admin/` del core | Todo: crear y pausar boliches, cambiar comisiones, cargar el equipo, cargar fechas y borrar. |
| Administración del boliche | `Membership` con rol `admin` | Dashboard del repo de su boliche (pendiente: API de gestión de fechas; la conexión de Mercado Pago ya está en `/api/v1/manage/`) | Fechas y entradas de su boliche; borra solo las que no tienen ventas. Ver [ABM de eventos](eventos-del-boliche.md) y [Conexiones](conexiones.md). |
| Puerta | `Membership` con rol `door` | API de portería (`/api/v1/door/`) con el login del personal (`/api/v1/auth/`) | Escanear entradas (cada QR firmado se acepta una sola vez) y ver la asistencia por evento. Funciona solo con conexión. |
| Caja | `Membership` con rol `cashier` | Pendiente | Reservado para la venta en caja. |

- **El core es solo de la plataforma.** La gente de los boliches nunca entra a `/plataforma/` ni al `/admin/` del core: trabaja desde el dashboard del repo de su boliche. Ver [Clientes](clientes.md).
- Un usuario puede tener una membresía por boliche, con un rol en cada uno. La membresía dice qué boliche puede tocar desde la API de gestión.
- **Debe:** cargar a alguien en el equipo de un boliche no le da `is_staff`. La migración `0005` se lo quitó a los miembros que lo tenían.

## Reglas de aislamiento

- **Debe:** si un usuario de staff que no es superusuario tiene membresías, el `/admin/` igual le muestra solo eventos y tipos de entrada de sus boliches. Abrir uno ajeno por URL redirige al índice. Es una defensa extra: en la operación normal solo entran superusuarios.
- **Debe:** al crear un evento o tipo de entrada, solo se pueden elegir boliches o eventos propios. Un formulario forjado con un evento ajeno se rechaza.
- El filtro por boliche del listado de eventos ofrece solo los boliches que el usuario ya ve.
- **Debe:** la API pública resuelve siempre `/<boliche>/<evento>/`. El mismo slug de evento en otro boliche es otra fecha.
- **Debe:** en el panel, una membresía se busca por boliche y por id. Usar la URL de un boliche con la membresía de otro da 404.
- Las pruebas usan siempre un segundo boliche para detectar accesos cruzados.

- Los admins de pedidos, entradas y pagos del `/admin/` (`OrderAdmin`, `TicketAdmin`, `PaymentAdmin`) son herramientas de la plataforma. Igual limitan lo que ven al boliche de las membresías del usuario y no exponen datos de compradores de otros boliches.

## Pendiente

- Modo sin señal del escáner de puerta, que necesita una regla de operación para cuando dos celulares sin conexión aceptan el mismo QR.
- Herramientas para el rol de caja.
- Credenciales de API por repo de cliente, limitadas a su boliche. Ver [Clientes](clientes.md).
