# ABM de eventos del boliche

Lo que puede hacer el equipo de administración de un boliche con sus fechas y entradas, desde el dashboard del repo de su boliche ([Clientes](clientes.md)). Se toma como referencia el dashboard `/admin` de TicketJuy, el repo de Infinity, que el dueño ya usa en modo demo.

## Estado actual

Hoy hay dos implementaciones que no están conectadas:

| | Dashboard de TicketJuy (`/admin`) | Admin de Django del core (`/admin/`) |
| --- | --- | --- |
| Quién lo usa | El dueño de Infinity | Solo el equipo de la plataforma |
| Dónde guarda | `localStorage` del navegador (modo demo) | La base del core |
| Uso | Pensado para el dueño, en el celular | Formularios genéricos de Django |
| Ingreso | Sin login | Superusuario del core |

Lo que carga el dueño en el dashboard de TicketJuy **no llega** al catálogo público: el dashboard sigue en modo demo. La API de gestión (`/api/v1/manage/`) ya existe para conectarlo; hasta entonces, las fechas reales de Infinity las carga el equipo de la plataforma desde el admin del core, que queda para soporte.

## Quién puede

- Los usuarios con membresía `admin` en el boliche, desde el dashboard de su sitio con un token de la API de gestión. Ver [Multi-boliche](multi-boliche.md).
- El equipo de la plataforma, desde el admin del core.
- Puerta y caja no editan el catálogo.
- Un administrador nunca ve ni toca fechas de otro boliche.

## Datos de una fecha

| Campo | En el core | Reglas |
| --- | --- | --- |
| Título | `Event.title` | Obligatorio, hasta 200 caracteres |
| Identificador | `Event.slug` | Se genera del título al crear: minúsculas, sin acentos, hasta 60 caracteres. Si ya existe en el boliche, se agrega `-2`, `-3`… **No cambia al editar**, para no romper los links compartidos |
| Artistas | `Event.lineup` | Lista. El panel los carga separados por coma, el core los guarda uno por línea |
| Descripción | `Event.description` | Opcional |
| Noche y hora | `Event.starts_at` | Ver "Fecha de la noche" abajo. Al crear, no puede estar en el pasado |
| Ingreso | `Event.doors_note` | Opcional, por ejemplo "Hasta las 3:00" |
| Edad mínima | `Event.age_limit` | De 0 a 99, por defecto 18 |
| Dress code | `Event.dress_code` | Opcional |
| Portada | **Falta** | Foto propia de la fecha. El panel la achica en el navegador a 1600 px de lado mayor, en JPEG |
| Venta abierta | `Event.sales_open` | Si está en no, la fecha se ve pero no se venden entradas |
| Publicada | `Event.is_published` | Si está en no, la fecha no se ve. El panel de TicketJuy todavía no la muestra |

### Fecha de la noche

El dueño piensa en "la noche del viernes" aunque la fiesta empiece el sábado a las 00:30. El formulario pide la noche y la hora. Si la hora es anterior a las 8:00, la fecha real es el día siguiente. Jujuy está en UTC−3 todo el año. Ejemplo: noche del viernes 20/11 a las 00:30 → `2026-11-21T00:30:00-03:00`.

## Datos de un tipo de entrada

| Campo | En el core | Reglas |
| --- | --- | --- |
| Identificador | `TicketType.slug` | Se genera al crear (`general-1`, `vip-2`) y no cambia. Lo usan los pedidos |
| Tipo | `TicketType.kind` | Común o VIP. El panel agrupa las entradas y los gráficos por tipo |
| Nombre | `TicketType.name` | Obligatorio, por ejemplo "Anticipada, 2ª tanda" |
| Qué incluye | `TicketType.description` | Opcional. Por defecto "Ingreso general." o "Fila VIP en la puerta y acceso al sector VIP." |
| Precio | `TicketType.price` | Mayor a 0. El panel lo pide en pesos enteros, el core acepta centavos |
| Cupo | `TicketType.capacity` | Mayor a 0 y nunca menor que vendidas + reservas vigentes |
| Vendidas | `TicketType.sold` | Solo lectura. Nunca sale de un formulario |
| Activa | `TicketType.is_active` | Si está en no, no se vende. El panel de TicketJuy todavía no la muestra |

Las tandas (1ª, 2ª…) son tipos de entrada distintos con su propio precio y cupo.

## Operaciones

### Alta

- Crea la fecha con al menos un tipo de entrada. Por defecto propone una común y una VIP.
- **Debe:** la fecha se crea en el boliche del usuario, nunca en otro.

### Edición

- Se editan todos los campos de la fecha y sus entradas salvo el identificador y las vendidas.
- Se pueden agregar tipos de entrada en cualquier momento.
- **Debe:** el cupo no puede bajar de vendidas + reservas vigentes. Lo validan el admin de Django y la API de gestión bloqueando la fila; el panel de TicketJuy solo mira las vendidas.
- Un tipo de entrada **con ventas** no se puede quitar: se baja el cupo o se desactiva.
- Un tipo de entrada **sin ventas** se puede quitar.
- Un cambio de precio aplica a las reservas nuevas. Los pedidos hechos conservan su precio.

### Pausar y reanudar la venta

- Disponible en fechas que no pasaron. El admin del core no lo valida: `sales_open` queda editable en cualquier fecha.
- Con la venta pausada, la fecha sigue visible pero no acepta reservas: la API responde 409 `sales_paused`.
- No afecta los pedidos ya reservados o pagados.

### Baja

- Solo se puede eliminar una fecha **sin entradas vendidas**. Con ventas, se pausa la venta.
- Pide confirmación: la fecha no se puede recuperar.
- La API de gestión devuelve 409 `has_sales` si hay vendidas y 409 `has_orders` si hay pedidos (aunque no tengan ventas: los pedidos protegen la fecha a nivel base, `PROTECT`). En el admin del core el superusuario borra siempre; el administrador del boliche, solo fechas y tipos de entrada sin ventas ni pedidos ni entradas emitidas.

### Listado y resumen

- Fechas en dos grupos: próximas, de la más cercana a la más lejana, y finalizadas, de la más reciente a la más vieja.
- Cada fecha muestra su portada, la noche y la hora, vendidas sobre cupo y su estado.
- **Estado**, en este orden: "Finalizado" si ya empezó, "Venta pausada", "Agotado" si todas las entradas están vendidas, o "A la venta".
- Detalle de una fecha: vendidas, disponibles, recaudación y ticket promedio, y las ventas por tipo de entrada.
- Resumen del boliche: vendidas por fecha, ocupación por tipo (común, VIP y libres) y recaudación frente a la potencial.
- La recaudación se calcula a precio de entrada, sin cargo de servicio. Pendiente: con pagos reales, sale de los pedidos pagados y descuenta la [comisión de la plataforma](comisiones.md).

## API de gestión

`/api/v1/manage/` es el contrato entre el core y el dashboard de cada repo de cliente. La consume **el servidor de Next**, nunca el navegador del dueño.

### Autenticación

- Cabecera `Authorization: Bearer <clave>` con una clave por usuario (`ApiToken`, `secrets.token_urlsafe(32)`). En la base solo queda el hash (`sha256`) y los últimos 6 caracteres como referencia.
- La plataforma la genera con `manage.py create_api_token <email>`: la clave se muestra una sola vez.
- El usuario del token tiene que administrar el boliche de la URL (membresía `admin`) o ser superusuario.
- En este orden: sin clave, clave malformada o inactiva → 401 `unauthorized`; boliche inexistente → 404 `venue_unavailable`; sin permiso sobre el boliche → 403 `forbidden`.

### Operaciones

| Método | Ruta | Acción |
| --- | --- | --- |
| GET | `/api/v1/manage/venues/<boliche>/events/` | Listado con vendidas, cupo, reservas vigentes y estado, incluidos borradores y finalizados |
| POST | `/api/v1/manage/venues/<boliche>/events/` | Alta de la fecha con sus entradas, en una transacción |
| GET | `/api/v1/manage/venues/<boliche>/events/<evento>/` | Detalle con los tipos de entrada (vendidas y reservas por tipo) |
| PATCH | `/api/v1/manage/venues/<boliche>/events/<evento>/` | Edición, incluido pausar o reanudar la venta, y altas o desactivaciones de entradas |
| DELETE | `/api/v1/manage/venues/<boliche>/events/<evento>/` | Baja, solo sin ventas |

### Cuerpo de la fecha

`title` (obligatorio, ≤200), `startsAt` (ISO 8601, obligatorio y futuro en el alta), `lineup` (lista de artistas o texto), `description`, `doorsNote` (≤200), `ageLimit` (0–99, por defecto 18), `dressCode` (≤120), `isPublished` (por defecto `false`) y `salesOpen` (por defecto `true`). El `slug` se genera del título y **nunca se acepta** en el cuerpo.

### Tipos de entrada

- En el alta, `ticketTypes` es obligatorio con al menos un ítem: `{ name, kind, price, capacity, description?, isActive? }`. `name` ≤150, `kind` `common` (por defecto) o `vip`, `price` y `capacity` mayores a 0. Sin `description`, queda la de su tipo ("Ingreso general." / "Fila VIP en la puerta y acceso al sector VIP."). Los slugs se generan `general-1`, `vip-1`…
- En la edición, un ítem **con** `slug` modifica ese tipo; uno **sin** `slug` lo crea. `isActive: false` lo desactiva. `sold` nunca se acepta.

### Respuestas

Cada fecha trae `slug`, `title`, `startsAt`, `isPublished`, `salesOpen`, `sold`, `capacity`, `reserved` y `status` (`finished` si ya empezó, si no `paused`, `sold_out` si no queda cupo o `on_sale`; un borrador se distingue por `isPublished: false`). El detalle suma `lineup`, `description`, `doorsNote`, `ageLimit`, `dressCode` y `ticketTypes` con `id`, `name`, `kind`, `description`, `price` (número), `capacity`, `sold`, `reserved` e `isActive`. El alta devuelve 201 y la edición 200 con la fecha; la baja, 204.

### Errores

JSON `{"error": codigo}`, como en la API pública (415 `expected_json`, 413 `request_too_large`).

| Error | Cuándo |
| --- | --- |
| 400 `invalid_request` | JSON inválido o no es un objeto |
| 400 `invalid_title`, `invalid_starts_at`, `invalid_lineup`, `invalid_age_limit`, `invalid_doors_note`, `invalid_dress_code`, `invalid_description`, `invalid_is_published`, `invalid_sales_open`, `invalid_slug` | Campo de la fecha ausente o fuera de regla; `slug` en el cuerpo |
| 400 `invalid_ticket_types`, `invalid_name`, `invalid_kind`, `invalid_price`, `invalid_capacity`, `invalid_is_active`, `invalid_sold` | Entradas ausentes, repetidas, con `slug` desconocido o fuera de regla; `sold` en el cuerpo |
| 404 `event_unavailable` | La fecha no existe en el boliche |
| 409 `capacity_below_sales` | El cupo queda por debajo de vendidas + reservas vigentes |
| 409 `has_sales` | Baja de una fecha con entradas vendidas |
| 409 `has_orders` | Baja de una fecha con pedidos |

## Lo que falta en el core

1. **Campos nuevos**:
   - Portada de la fecha, guardada como archivo y no como data URL. Requiere decidir dónde se guardan los archivos.
2. **Login del dueño** desde el dashboard de su sitio. Hoy la plataforma genera los tokens con `manage.py create_api_token`; falta que el dueño entre con su email y contraseña y reciba el token sin intervención de la plataforma. Además, el repo del cliente va a usar su propia credencial ([Clientes](clientes.md)).
