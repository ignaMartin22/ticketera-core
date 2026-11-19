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

Lo que carga el dueño en el dashboard de TicketJuy **no llega** al catálogo público. Hasta conectar la API de gestión, las fechas reales de Infinity las carga el equipo de la plataforma desde el admin del core. El objetivo es que cada dashboard de cliente use la API de gestión y que el admin de Django quede para soporte.

## Quién puede

- Los usuarios con membresía `admin` en el boliche, desde el dashboard de su sitio. Ver [Multi-boliche](multi-boliche.md).
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
- **Debe:** el cupo no puede bajar de vendidas + reservas vigentes. Hoy lo valida el admin de Django bloqueando la fila; el panel de TicketJuy solo mira las vendidas.
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
- En el admin del core el superusuario borra siempre; el administrador del boliche, solo fechas y tipos de entrada sin ventas. Los pedidos protegen la fecha a nivel base (`PROTECT`): una fecha con pedidos no se borra.

### Listado y resumen

- Fechas en dos grupos: próximas, de la más cercana a la más lejana, y finalizadas, de la más reciente a la más vieja.
- Cada fecha muestra su portada, la noche y la hora, vendidas sobre cupo y su estado.
- **Estado**, en este orden: "Finalizado" si ya empezó, "Venta pausada", "Agotado" si todas las entradas están vendidas, o "A la venta".
- Detalle de una fecha: vendidas, disponibles, recaudación y ticket promedio, y las ventas por tipo de entrada.
- Resumen del boliche: vendidas por fecha, ocupación por tipo (común, VIP y libres) y recaudación frente a la potencial.
- La recaudación se calcula a precio de entrada, sin cargo de servicio. Pendiente: con pagos reales, sale de los pedidos pagados y descuenta la [comisión de la plataforma](comisiones.md).

## Lo que falta en el core

1. **Campos nuevos**:
   - Portada de la fecha, guardada como archivo y no como data URL. Requiere decidir dónde se guardan los archivos.
2. **Identificadores fijos**: hoy el admin de Django deja cambiar el slug de una fecha o de una entrada ya creada. Tiene que quedar de solo lectura después del alta.
3. **Reglas de baja**: no eliminar fechas con ventas ni tipos de entrada con ventas. La baja de una fecha sin ventas tiene que estar en la API de gestión, para el administrador del boliche.
4. **API de gestión** para el dashboard de cada cliente, con autenticación por usuario y limitada a los boliches que administra. Por ejemplo:

   | Método | Ruta | Acción |
   | --- | --- | --- |
   | GET | `/api/v1/manage/venues/<boliche>/events/` | Listado con vendidas, cupo, reservas y estado, incluidos borradores y finalizados |
   | POST | `/api/v1/manage/venues/<boliche>/events/` | Alta con sus entradas |
   | GET | `/api/v1/manage/venues/<boliche>/events/<evento>/` | Detalle |
   | PATCH | `/api/v1/manage/venues/<boliche>/events/<evento>/` | Edición, incluido pausar o reanudar la venta |
   | DELETE | `/api/v1/manage/venues/<boliche>/events/<evento>/` | Baja, solo sin ventas |

   Falta decidir cómo se loguea el dueño en el dashboard de su sitio: por ejemplo, el servidor de Next manda email y contraseña al core y recibe un token de ese usuario. Además, el repo del cliente usa su propia credencial ([Clientes](clientes.md)).
5. **Validaciones del servidor**: que el core aplique las reglas del panel (fecha futura al crear, precio y cupo mayores a 0, al menos una entrada). El panel las valida antes de enviar, pero el servidor tiene que validarlas de nuevo.
6. **Pruebas** de aislamiento, de baja con y sin ventas y de identificadores fijos.
