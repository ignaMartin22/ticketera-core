# Guía de integración para el front

Cómo se conecta TicketJuy (Next.js) con ticketera-core (Django). Estado al 19/11/2026.

## Reglas generales

- **El navegador nunca habla con esta API.** Todas las llamadas salen del servidor de Next (páginas de servidor, server actions o route handlers). Por eso la API no tiene CORS ni cookies propias.
- **Base de la API**: variable `TICKETERA_API_URL` en el front. Sin ella, el front usa sus datos de prueba.
- **Multi-boliche**: toda ruta lleva el slug del boliche. Los slugs de evento y de tipo de entrada son únicos dentro de su boliche, no globales. Nunca asumas `infinity`.
- **Formato**: JSON, claves en `camelCase`. Fechas en ISO 8601 con zona horaria. Importes de pedidos como texto decimal (`"10000.00"`); precios del catálogo como número.
- **Errores**: `{ "error": "<código>" }` con el estado HTTP. El escaneo usa `{ "result": "<código>" }`. Los códigos son estables: traducilos en el front, no muestres el código crudo.
- **Caché**: el catálogo se puede cachear un minuto. Pedidos, portería y todo lo que lleva token van con `cache: "no-store"`.

## Dónde está cada cosa

| Parte | Rama del backend | Estado en el front |
| --- | --- | --- |
| Catálogo y pedidos | `main` | Integrado |
| Entradas con QR, portería, asistencia | `feat/escaneo-puerta` | Integrado en la rama `feat/escaneo-puerta` de TicketJuy |
| Login único del personal | `feat/mercado-pago` | Sin integrar |
| Conexión de Mercado Pago del boliche | `feat/mercado-pago` | Sin integrar (va en el panel) |
| Link de pago con Mercado Pago | `feat/mercado-pago` | Sin integrar |
| API de gestión de fechas | `feat/mercado-pago` (integra #16) | Sin integrar (va en el dashboard) |
| Confirmación del pago (`sync`, `paymentReview`) | `feat/mercado-pago` | En construcción en el backend |

## 1. Catálogo público

Sin autenticación.

| GET | Respuesta |
| --- | --- |
| `/api/v1/venues/` | `{ venues: Venue[] }`, solo boliches activos |
| `/api/v1/venues/<boliche>/events/` | `{ venue, events: Event[] }`, solo eventos publicados |
| `/api/v1/venues/<boliche>/events/<evento>/` | `{ venue, event }` |

```ts
type Venue = {
  slug: string; name: string; city: string; address: string; instagram: string;
  tagline: string | null; logo: string | null; cover: string | null;
  flyerBackdrop: string | null; mapsUrl: string | null;
  accent: string;                 // "#ee10b8"
  feeMode: "venue" | "buyer";     // quién absorbe el cargo de servicio
  buyerFeeRate: number;           // 0.1 = 10 %
};

type Event = {
  slug: string; venueSlug: string; title: string;
  lineup: string[]; description: string; startsAt: string;
  doorsNote: string; ageLimit: number; dressCode: string | null;
  ticketTypes: {
    id: string;                   // slug del tipo, único dentro del evento
    name: string; description: string;
    price: number; capacity: number; sold: number;
    available: number;            // lo que queda: descuenta vendidas y reservas vigentes
  }[];
};
```

Usá `available` para mostrar cupo y agotados. `404` si el boliche está inactivo o el evento no está publicado.

## 2. Pedidos sin cuenta

### Crear: `POST /api/v1/venues/<boliche>/events/<evento>/orders/`

Cabeceras: `Content-Type: application/json` e `Idempotency-Key: <uuid>`. El navegador genera la clave una vez por intento de compra y la repite si reintenta.

```json
{
  "email": "comprador@example.com",
  "name": "Juana Pérez",
  "phone": "+5493884123456",
  "items": [{ "ticketTypeId": "general", "quantity": 2 }]
}
```

`phone` es opcional (las entradas todavía no se envían por WhatsApp): se puede omitir o mandar vacío. Si viene, tiene que ser E.164 (`+549` y los 10 dígitos para celulares argentinos).

Respuesta `201` (o `200` si es un reintento con la misma clave y el mismo contenido): `{ order: Order }`.

| Estado | `error` | Qué hacer |
| --- | --- | --- |
| 400 | `invalid_email`, `invalid_name`, `invalid_phone`, `invalid_items`, `order_limit_exceeded`, `invalid_request` | Marcar el campo. El máximo es 10 entradas por pedido. |
| 404 | `event_unavailable` | La fecha ya no se vende. |
| 409 | `insufficient_capacity`, `ticket_type_unavailable` | Cambió el cupo: refrescar y volver a elegir. |
| 409 | `idempotency_key_reused` | Misma clave con otro contenido: generar una clave nueva. |
| 413 / 415 | `request_too_large`, `expected_json` | Error de integración. |

### Consultar: `GET /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/`

```ts
type Order = {
  id: string;                                   // uuid: es el dato privado del comprador
  status: "pending_payment" | "expired" | "paid";
  expiresAt: string;                            // la reserva dura 10 minutos
  currency: "ARS";
  total: string;                                // incluye el cargo de servicio
  items: { ticketTypeId: string; quantity: number; unitPrice: string; unitServiceFee: string }[];
  tickets: OrderTicket[];                       // vacío si no está pagado
};

type OrderTicket = {
  id: string;
  ticketTypeId: string;
  ticketTypeName: string;
  qr: string;                                   // texto exacto que va dentro del QR
  usedAt: string | null;                        // cuándo se quemó en puerta
};
```

- Quien tiene el `id` del pedido ve sus entradas. No lo pongas en logs ni en analíticas.
- La respuesta no trae nombre, email ni teléfono del comprador.
- `qr` se dibuja tal cual, sin modificarlo ni agregarle una URL.
- **Pedido gratis**: si el total es $0, la respuesta del `POST` ya viene con `status: "paid"` y las entradas. Redirigí a la página del pedido sin pasar por el pago.
- **Pedido con precio**: queda en `pending_payment` hasta que se confirme el pago. Hoy solo se confirma a mano desde el admin de Django ("Marcar como pagado y emitir entradas").

## 3. Personal: login y permisos

El personal entra con su usuario. El rol se define por boliche: `admin`, `door` o `cashier`. Una persona puede tener roles distintos en boliches distintos.

### `POST /api/v1/auth/login/`

Cuerpo `{ "username", "password" }`. Respuesta `200`:

```json
{
  "token": "...",
  "expiresIn": 43200,
  "user": { "username": "juana" },
  "memberships": [{ "venue": { "slug": "infinity", "name": "Infinity" }, "role": "admin" }]
}
```

| Estado | `error` | Significado |
| --- | --- | --- |
| 401 | `invalid_credentials` | Usuario o contraseña incorrectos. |
| 403 | `no_membership` | El usuario no pertenece a ningún boliche activo. |
| 429 | `too_many_attempts` | 10 fallos en 15 minutos. Trae `retryAfter` en segundos. |

### `GET /api/v1/auth/me/`

Con el token, devuelve `{ user, memberships }` leído en el momento. Sirve para armar el selector de boliche y decidir qué menú mostrar.

### Cómo usar el token

- Va en la cabecera `Authorization: Bearer <token>` de cada llamada del servidor de Next.
- Dura 12 horas. Guardalo en una cookie `httpOnly`, nunca en `localStorage` ni en JavaScript del navegador.
- `401 unauthorized`: el token falta, venció o el usuario fue desactivado. Borrar la cookie y mandar al login.
- `404 not_found`: el usuario no tiene el rol necesario en ese boliche. La API no distingue "no existe" de "no tenés permiso".

`POST /api/v1/door/login/` sigue existiendo para la portería: mismo token, pero solo acepta roles `door` y `admin`, y devuelve `venues` en vez de `memberships`. Para pantallas nuevas usá `auth/login`.

## 4. Portería

Roles `door` o `admin` del boliche. Ya integrada en `/puerta` de TicketJuy.

| Ruta | Uso |
| --- | --- |
| `GET /api/v1/door/venues/<boliche>/events/` | `{ events: [{ slug, title, startsAt }] }`, para elegir la fecha |
| `POST /api/v1/door/venues/<boliche>/events/<evento>/scan/` | Cuerpo `{ "token": "<texto del QR>" }`. Quema la entrada. |
| `GET /api/v1/door/venues/<boliche>/events/<evento>/attendance/` | Asistencia de la fecha |

Respuestas del escaneo:

| Estado | `result` | Cuerpo | Significado |
| --- | --- | --- | --- |
| 200 | `admitted` | `ticket: { id, ticketType, holderName, usedAt }` | Ingreso válido. Queda quemada. |
| 409 | `already_used` | `ticket` con `usedAt` y `usedBy` del primer escaneo | Ya ingresó. |
| 409 | `wrong_event` | `event: { slug, title, startsAt }` | Es de otra fecha del boliche. No se quema. |
| 404 | `invalid` | | QR falso, alterado o de otro boliche. |

El escaneo solo funciona con conexión. Ante un error de red no asumas que la entrada entró: se puede reintentar sin riesgo, porque el segundo intento responde `already_used` si el primero llegó.

Asistencia:

```ts
type Attendance = {
  event: { slug: string; title: string; startsAt: string };
  issued: number; admitted: number;
  ticketTypes: { id: string; name: string; issued: number; admitted: number }[];
  tickets: { id: string; holderName: string; ticketType: string; usedAt: string | null; usedBy: string | null }[];
};
```

`tickets` trae primero las ingresadas (la más reciente arriba) y después las que no ingresaron.

## 5. Mercado Pago: conexión del boliche

Rol `admin` del boliche.

| Ruta | Uso |
| --- | --- |
| `POST /api/v1/manage/venues/<boliche>/mercadopago/connect/` | Devuelve `{ authorizationUrl }`. El front redirige el navegador a esa URL. |
| `GET /api/v1/manage/venues/<boliche>/mercadopago/` | Estado de la conexión |
| `DELETE /api/v1/manage/venues/<boliche>/mercadopago/` | Desconecta. Responde `204`. |

Estado:

```ts
type MercadoPagoStatus = {
  connected: boolean;
  needsReconnect: boolean;    // el boliche revocó el permiso o falló la renovación
  mpUserId: number | null;
  liveMode: boolean | null;   // false = credenciales de prueba
  connectedAt: string | null;
  expiresAt: string | null;
};
```

Flujo para el panel:

1. El admin toca "Conectar Mercado Pago". Un server action llama a `connect` y redirige a `authorizationUrl`.
2. El dueño autoriza en Mercado Pago.
3. Mercado Pago vuelve al backend, que guarda los tokens y redirige el navegador a la URL del panel configurada en `MP_CONNECT_RETURN_URL`, con `?mp=connected&venue=<slug>` o `?mp=error&reason=<código>`.
4. El panel lee esos parámetros, muestra el resultado y pide el estado.

Códigos de `reason`: `denied` (el dueño canceló), `invalid_state` (el link venció a los 10 minutos o es de otra persona), `exchange_failed`, `not_configured`. Si la integración no tiene credenciales, las rutas responden `503 mp_not_configured`.

Los tokens se renuevan solos. Mostrá "Reconectar" solo cuando `needsReconnect` es `true`.

## 6. Mercado Pago: pago del pedido

El front no usa ningún SDK ni credencial de Mercado Pago. No hay public key en el navegador: el backend arma el pago con la cuenta del boliche y devuelve una URL. El front solo redirige.

### Pedir el link: `POST /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/payment/`

Sin cuerpo y sin token: alcanza con el `id` del pedido. Ya está en la rama `feat/mercado-pago`.

Respuesta `200`: `{ "initPoint": "https://www.mercadopago.com.ar/checkout/..." }`. Redirigí el navegador a esa URL tal cual. Es idempotente: pedirlo dos veces devuelve el mismo link.

| Estado | `error` | Qué mostrar |
| --- | --- | --- |
| 404 | `not_found` | El pedido no existe en ese boliche y esa fecha. |
| 409 | `order_expired` | La reserva venció: volver a elegir entradas. |
| 409 | `order_already_paid` | Ya está pago: ir a la página del pedido. |
| 409 | `venue_not_connected` | El boliche todavía no conectó Mercado Pago. "El pago online no está disponible para esta fecha." |
| 502 | `mp_unavailable` | Mercado Pago no respondió: reintentar. |
| 503 | `mp_not_configured` | El backend no tiene las credenciales cargadas. Solo en entornos sin configurar. |

El link vence junto con la reserva (10 minutos). No se ofrece pago en efectivo.

### La vuelta del comprador

Mercado Pago devuelve al comprador a `/<boliche>/<evento>/pedido/<uuid>` del front, con parámetros propios en la URL. No confíes en esos parámetros: el estado real lo dice el backend.

La confirmación llega al backend por un aviso de Mercado Pago que puede tardar unos segundos. Para no depender de eso, al cargar la página del pedido:

1. Si el pedido está en `pending_payment`, llamá a `POST /api/v1/venues/<boliche>/events/<evento>/orders/<uuid>/payment/sync/` (sin cuerpo). Responde `{ order }` con el estado actualizado.
2. Si sigue en `pending_payment`, repetí cada 3 a 5 segundos mientras la reserva esté vigente.
3. Cuando pasa a `paid`, mostrá las entradas (`order.tickets`).

`sync` y el campo `paymentReview` están en construcción en el backend; el contrato es este.

### Pago recibido sin entradas

`order.paymentReview` es `true` cuando el pago se aprobó pero no se pudieron emitir las entradas (por ejemplo, llegó con la reserva vencida y ya no había cupo). No hay reembolso automático. Mostrá: "Recibimos tu pago, pero no pudimos emitir tus entradas. Escribile al boliche con este número de pedido." y el `id` del pedido.

### Qué cambiar en el front

- **Botón "Pagar con Mercado Pago"** en la página del pedido: hoy está deshabilitado. Un server action llama a `payment`, y redirige a `initPoint` o muestra el error.
- **Página del pedido**: sumar el `sync` con reintento y el aviso de `paymentReview`.
- **Tipos**: agregar `paymentReview: boolean` a `Order`.
- **Variables de entorno**: ninguna nueva. Sigue alcanzando con `TICKETERA_API_URL`.

Internamente el backend usa la API de Orders de Mercado Pago (el `initPoint` es el `checkout_url` de la order). Para el front no cambia nada: el campo sigue siendo `initPoint`. Tampoco cambia con el cobro directo (un boliche que cobra en una cuenta cargada a mano, sin `marketplace_fee`): el contrato, el total y los errores son los mismos.

## 7. API de gestión de fechas

Rol `admin` del boliche (o superusuario). Es la API para el dashboard del dueño: alta, edición, pausa y baja de fechas con sus entradas. Rutas bajo `/api/v1/manage/venues/<boliche>/events/`.

### Autenticación

Va en `Authorization: Bearer <token>`. Sirve **el token de `POST /api/v1/auth/login/`** (12 horas, ver la sección 3): el dueño entra con su usuario y el servidor de Next usa ese token. También se acepta una clave de larga duración (`ApiToken`) que la plataforma crea con `manage.py create_api_token <email>`, útil para scripts. El usuario tiene que ser `admin` del boliche de la URL; la regla es la misma con cualquiera de los dos tokens.

### Rutas

| Ruta | Uso |
| --- | --- |
| `GET /api/v1/manage/venues/<boliche>/events/` | Listado: borradores, finalizadas y pausadas incluidas. Responde `{ events: [...] }`. |
| `POST /api/v1/manage/venues/<boliche>/events/` | Alta de la fecha con sus entradas. Responde `201 { event }`. |
| `GET /api/v1/manage/venues/<boliche>/events/<evento>/` | Detalle `{ event }` con los tipos de entrada. |
| `PATCH /api/v1/manage/venues/<boliche>/events/<evento>/` | Edición parcial (incluye pausar la venta). Responde `200 { event }`. |
| `DELETE /api/v1/manage/venues/<boliche>/events/<evento>/` | Baja, solo sin ventas ni pedidos. Responde `204`. |

Las rutas de Mercado Pago (sección 5) viven bajo el mismo prefijo `manage/venues/<boliche>/mercadopago/`.

### Cuerpos

Alta (`Content-Type: application/json`):

```json
{
  "title": "Noche de prueba",
  "startsAt": "2026-12-05T01:00:00-03:00",
  "lineup": ["DJ Uno", "DJ Dos"],
  "description": "",
  "doorsNote": "Apertura 23:30",
  "ageLimit": 18,
  "dressCode": null,
  "isPublished": false,
  "salesOpen": true,
  "ticketTypes": [
    { "name": "Anticipada", "kind": "common", "price": 5000, "capacity": 100 },
    { "name": "VIP", "kind": "vip", "price": 12000, "capacity": 20, "description": "Fila VIP", "isActive": true }
  ]
}
```

- Obligatorios: `title` (hasta 200 caracteres), `startsAt` (ISO 8601, futuro en el alta) y `ticketTypes` con al menos un ítem. El resto es opcional.
- `lineup` es una lista de artistas o un texto con uno por línea. `ageLimit` va de 0 a 99 (por defecto 18). `isPublished` por defecto `false`; `salesOpen` por defecto `true`.
- El `slug` de la fecha se genera del título y el de las entradas es `general-1`, `vip-1`... **Nunca se manda** `slug` al crear ni `sold`.
- Cada entrada: `name` (hasta 150), `kind` (`common` por defecto o `vip`), `price` y `capacity` mayores a 0, `description` y `isActive` opcionales.

Edición: se mandan solo los campos que cambian, por ejemplo `{ "salesOpen": false }` para pausar. En `ticketTypes`, un ítem **con** `slug` (el `id` que devuelve la API) modifica esa entrada; uno **sin** `slug` crea una nueva; `isActive: false` la desactiva. No se puede cambiar el `slug` de la fecha ni mandar `sold`.

Respuesta de cada fecha:

```ts
type ManagedEvent = {
  slug: string; title: string; startsAt: string;
  isPublished: boolean; salesOpen: boolean;
  sold: number; capacity: number; reserved: number;   // reserved = reservas vigentes
  status: "finished" | "paused" | "sold_out" | "on_sale";
  // solo en el detalle, el alta y la edición:
  lineup: string[]; description: string; doorsNote: string; ageLimit: number; dressCode: string | null;
  ticketTypes: { id: string; name: string; kind: "common" | "vip"; description: string; price: number;
                 capacity: number; sold: number; reserved: number; isActive: boolean }[];
};
```

### Errores

| Estado | `error` | Cuándo |
| --- | --- | --- |
| 401 | `unauthorized` | Token faltante, inválido, vencido o de un usuario inactivo. |
| 403 | `forbidden` | El usuario no es `admin` de ese boliche (puerta, caja u otro boliche). |
| 404 | `venue_unavailable` | El boliche no existe. |
| 404 | `event_unavailable` | La fecha no existe en ese boliche. |
| 405 | `method_not_allowed` | Método no soportado en la ruta. |
| 413 | `request_too_large` | Cuerpo de más de 8 KB. |
| 415 | `expected_json` | Falta `Content-Type: application/json`. |
| 400 | `invalid_request` | JSON inválido o no es un objeto. |
| 400 | `invalid_title`, `invalid_starts_at`, `invalid_lineup`, `invalid_age_limit`, `invalid_doors_note`, `invalid_dress_code`, `invalid_description`, `invalid_is_published`, `invalid_sales_open`, `invalid_slug` | Campo de la fecha fuera de regla, o `slug` en el cuerpo. |
| 400 | `invalid_ticket_types`, `invalid_name`, `invalid_kind`, `invalid_price`, `invalid_capacity`, `invalid_is_active`, `invalid_sold` | Entradas ausentes, repetidas, con `slug` desconocido o fuera de regla; `sold` en el cuerpo. |
| 409 | `capacity_below_sales` | El cupo quedaría por debajo de vendidas + reservas vigentes. |
| 409 | `has_sales` | Baja de una fecha con entradas vendidas (pausá la venta en su lugar). |
| 409 | `has_orders` | Baja de una fecha que tiene pedidos. |

Con la venta pausada, la API pública sigue mostrando la fecha (`salesOpen: false`) y `POST .../orders/` responde `409 sales_paused`; los pedidos ya reservados siguen pudiendo pagarse.

## Pendiente del lado del front

1. **Pedido gratis**: redirigir del checkout a la página del pedido cuando el `POST` ya vuelve en `paid`.
2. **Actualizar la página del pedido**: mientras está en `pending_payment`, volver a consultar cada pocos segundos para mostrar las entradas apenas se confirme.
3. **`/entrada/[code]`**: sigue con datos de prueba. Falta definir el endpoint de entrada individual.
4. **Panel `/admin`**: sigue en `localStorage` con `infinity` fijo. Necesita el login de la sección 3, un selector de boliche (`/admin/[boliche]/...`) y la API de gestión, que todavía no existe.
5. **Multi-boliche en el front**: `HOME_VENUE`, `ADMIN_VENUE_SLUG` y el manifiesto de la app instalable tienen a Infinity fijo.
6. **Probar la portería en celulares reales**: la lectura con cámara, la vibración y el vencimiento de sesión no se probaron en un dispositivo.

## Correr el backend en tu máquina

Hace falta Python 3.12 o superior y Docker.

```bash
docker run -d --name ticketera_pg -e POSTGRES_USER=ticketera -e POSTGRES_PASSWORD=ticketera_dev \
  -e POSTGRES_DB=ticketera -p 127.0.0.1:5433:5432 postgres:16-alpine

python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

export DJANGO_DEBUG=1 POSTGRES_DB=ticketera POSTGRES_USER=ticketera \
  POSTGRES_PASSWORD=ticketera_dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5433

.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_infinity
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8765
```

En el front: `TICKETERA_API_URL=http://127.0.0.1:8765 npm run dev`. Abrilo en `localhost`, no en `127.0.0.1`: con la IP, Next bloquea los recursos de desarrollo y la página no se hidrata.

Para probar la portería, creá un usuario con una membresía de rol `door` en el boliche `infinity` desde el equipo del boliche en `http://127.0.0.1:8765/plataforma/` (la gente de los boliches no recibe `is_staff`). Para tener entradas: creá un tipo de entrada de precio 0 en una fecha y comprala desde el front, o hacé un pedido con precio y aplicale "Marcar como pagado y emitir entradas" en el admin.

Tests del backend: `.venv/bin/python manage.py test catalog`.
