# Multi-boliche

Un solo core sirve a varios boliches. Cada boliche (`Venue`) es un tenant: todos comparten la base de datos y cada registro pertenece a un boliche.

## Modelo

```
Venue ─┬─ Membership ── User
       ├─ Event ── TicketType ── OrderItem ── Order
       ├─ FeeChange
       └─ VenueIntegration
```

- `Venue.slug` identifica al boliche en la API y en las URLs de TicketJuy (`/infinity`). Es único y no se cambia después del alta.
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
| Plataforma | Superusuarios de Django | `/plataforma/` y `/admin/` | Todo: crear y pausar boliches, cambiar comisiones, dar acceso y borrar. |
| Administración del boliche | `Membership` con rol `admin` | `/admin/` | Ver, crear y editar eventos y tipos de entrada **de sus boliches**. No puede borrar. |
| Puerta | `Membership` con rol `door` | Pendiente | Reservado para el escáner de puerta. |
| Caja | `Membership` con rol `cashier` | Pendiente | Reservado para la venta en caja. |

- Un usuario puede tener una membresía por boliche, con un rol en cada uno.
- `is_staff` se sincroniza solo: es `true` mientras el usuario sea administrador de al menos un boliche (o sea superusuario). Sin `is_staff`, Django no lo deja entrar a `/admin/`.

## Reglas de aislamiento

- **Debe:** el `/admin/` de un administrador de boliche lista, abre y edita solo eventos y tipos de entrada de los boliches que administra. Abrir uno ajeno por URL redirige al índice.
- **Debe:** al crear un evento o tipo de entrada, solo se pueden elegir boliches o eventos propios. Un formulario forjado con un evento ajeno se rechaza.
- El filtro por boliche del listado de eventos ofrece solo los boliches que el usuario ya ve.
- **Debe:** la API pública resuelve siempre `/<boliche>/<evento>/`. El mismo slug de evento en otro boliche es otra fecha.
- **Debe:** en el panel, una membresía se busca por boliche y por id. Usar la URL de un boliche con la membresía de otro da 404.
- Las pruebas usan siempre un segundo boliche para detectar accesos cruzados.

## Pendiente

- Admin de pedidos limitado por boliche, sin exponer datos de compradores de otros boliches.
- Herramientas para los roles de puerta y caja.
- Si un boliche tiene su propio dominio o frontend: resolver el boliche por dominio o con claves de API por cliente. Hoy hay un solo frontend, TicketJuy, con todos los boliches bajo `/<boliche>`.
