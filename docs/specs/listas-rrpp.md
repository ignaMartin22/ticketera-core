# Listas de RRPP

## Qué es
El boliche trabaja con RRPP. El admin crea, por fecha, una lista para cada RRPP con un cupo y una hora límite. El RRPP entra con su usuario y agrega gente (nombre + mail); a cada persona le llega por mail su entrada gratis con QR. Esa entrada sirve solo hasta la hora límite.

## Reglas
- Rol `rrpp` en `Membership`. Se asigna desde `/plataforma/boliches/<slug>/equipo/`. No da acceso a portería ni a la gestión del boliche.
- `GuestList`: una por (fecha, RRPP). Tiene un `TicketType` propio de tipo `list` (precio 0, creado junto con la lista). El cupo es `ticket_type.capacity` y lo emitido es `ticket_type.sold`: no hay contadores aparte.
- Los tipos `list` no se ven en el catálogo público, no se reservan por pedidos y no se editan por la API de tipos de entrada.
- Agregar un invitado = un `Order` pagado de total 0 con una entrada, emitido con `issue_tickets`. El mail sale por el mecanismo de siempre. Todo va en una transacción que bloquea la fecha y el tipo de la lista, así que cupo y unicidad no tienen carreras.
- Un mail (sin espacios, sin distinguir mayúsculas) tiene como máximo una entrada de lista por fecha, sumando todas las listas: `already_on_list`. En otra fecha sí puede estar. Haber comprado una entrada común no cuenta.
- Orden de chequeos al agregar: `list_closed` (inactiva o vencida), `already_on_list`, `quota_full`.
- El cupo no puede bajar de lo emitido (`quota_below_issued`). Inactiva solo impide agregar gente.
- Puerta: si `ahora > valid_until` y la entrada no se usó, el escaneo responde `list_closed` y no la quema. El quemado sigue siendo un UPDATE condicional sobre `used_at`. Una entrada ya usada responde `already_used`. La hora se compara con el mismo instante con el que se quema.
- El RRPP ve solo sus listas (aunque tenga varios boliches); un admin del boliche puede operar las de su boliche. Si pierde la membresía, pierde el acceso.
- Fuera de alcance: quitar o anular invitados, auto-inscripción por link, ranking de RRPP.

Contrato de la API: sección 8 de `docs/integracion-front.md`.
