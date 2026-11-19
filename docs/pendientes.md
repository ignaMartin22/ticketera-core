# Pendientes para el piloto con Infinity

Qué falta para vender una fecha real de Infinity con TicketJuy. Estado al 19/11/2026.

## Hecho

- Catálogo multi-boliche: boliches, membresías, eventos y tipos de entrada, con admin limitado por boliche (#2).
- Pedidos sin cuenta con reserva de cupo de 10 minutos e idempotencia (#3).
- Nombre y WhatsApp del comprador en el pedido, y `manage.py seed_infinity` para desarrollo (#4).
- Panel de la plataforma en `/plataforma/`, solo para el equipo de la plataforma: alta y pausa de boliches, comisión con historial (cargo al comprador y comisión de la plataforma), equipo de cada boliche y estado de las conexiones.
- La gente de los boliches no entra al core: cada cliente tiene su repo con su sitio y su dashboard. Ver las [specs](specs/README.md).
- Entradas con QR firmado de un solo uso: se emiten al reservar un pedido gratis o al marcar un pedido como pagado en el admin.
- API de portería: login del personal de puerta, escaneo que quema la entrada y asistencia por evento. Funciona solo con conexión.
- Mercado Pago: conexión OAuth del boliche, link de pago, webhook y conciliación, con la comisión de la plataforma como `marketplace_fee`.
- Despliegue con Docker en una VPS con PostgreSQL, backups y proxy HTTPS (`docs/despliegue-vps.md`).
- TicketJuy lee el catálogo de esta API y el checkout reserva el cupo (TicketJuy #12). La página del pedido muestra la reserva con el botón de pago deshabilitado.

## Para vender una fecha real

1. **Mercado Pago con split por boliche.** Hecho: vinculación de la cuenta del boliche por OAuth con renovación del token, link de pago con la API de Orders y `marketplace_fee` (cargo de servicio más comisión de la plataforma, con los importes guardados en el pedido), confirmación idempotente por webhook, conciliación de pagos sin notificación y emisión de entradas. Ver [conexiones](specs/conexiones.md) y [comisiones](specs/comisiones.md). Queda:
   - Decidir quién absorbe las comisiones propias de Mercado Pago.
   - Reembolsos, y qué pasa con los pagos marcados para revisión (`no_capacity`, `mismatch`, `duplicate`).
   - Botón de pago en la página del pedido de TicketJuy.
2. **Página de la entrada.** La API ya devuelve las entradas del pedido con el texto del QR. La página `/entrada/[code]` de TicketJuy todavía usa datos de prueba.
3. **Envío de entradas** por email y WhatsApp. La API de WhatsApp Business requiere aprobación de Meta y puede tardar: conviene pedirla ya.
4. **Escáner de puerta** en el navegador del celular. La API de escaneo y el login del personal, con límite de intentos, están hechos; falta la pantalla en TicketJuy que abre la cámara. Después, el modo sin señal con sincronización al volver, que necesita una regla de operación para cuando dos celulares sin conexión aceptan el mismo QR.
5. **Protección contra reservas automatizadas** en `POST .../orders/` (captcha o verificación de bots y límite por IP). Hoy un bot puede bloquear cupo de a 10 entradas durante 10 minutos, una y otra vez.

## Panel y operación

6. Para el piloto, cada boliche carga su catálogo en el admin de Django y la plataforma lo administra desde `/plataforma/`. Los pedidos y compradores por boliche ya se ven en la pestaña Ventas del panel (últimos 100, con estado: pagado, pendiente de pago o vencido).
7. Reporte de ventas por fecha en la pestaña Ventas del panel: vendidas, reservas vigentes, cupo, ocupación y recaudación por tipo de entrada. La recaudación es a precio de entrada; falta que salga de los pedidos pagados y descuente la comisión.
8. Conectar el dashboard `/admin` de TicketJuy (el repo de Infinity) al core según la [spec del ABM de eventos](specs/eventos-del-boliche.md). Hoy lo que carga el dueño queda en su navegador. La API de gestión ya existe; falta en el core:
   - Portada de la fecha.
   - Login del dueño desde su dashboard: hoy el token por usuario lo genera la plataforma con `manage.py create_api_token`.
   - Una credencial por repo de cliente, generada desde `/plataforma/` ([Clientes](specs/clientes.md)).

## Puesta en producción

9. Hosting del backend. **Resuelto para pruebas**: imagen Docker con gunicorn y whitenoise, PostgreSQL en contenedor con backup diario (`deploy/backup.sh`) y proxy HTTPS de Caddy en una VPS (ver `docs/despliegue-vps.md`), con `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` y `DJANGO_BEHIND_PROXY` configurados y `migrate` en cada arranque. Queda pendiente para producción real: PostgreSQL administrado (la app ya acepta `DATABASE_URL`), un dominio propio y sacar `MP_TEST_TOKENS`.
10. Frontend en Vercel con `TICKETERA_API_URL` apuntando al backend, y el dominio.
11. Monitoreo de errores (por ejemplo Sentry) y logs.

## Contenido y decisiones

12. Acordar la comisión con Infinity y cargarla en el panel. Hoy figura con un 10 % provisorio a cargo del comprador y 0 % de comisión de la plataforma.
13. Fechas, precios y aforo reales, flyers, Instagram y dirección exacta del boliche.
14. Términos y condiciones, política de privacidad (se guardan nombre, teléfono y email) y política de reembolsos. Consultar con un abogado si el botón de arrepentimiento aplica a entradas con fecha.
15. Probar la compra en celulares reales, también Android de gama baja.
16. Actualizar `docs/hoja-de-ruta.md` de TicketJuy: todavía dice que la base va en Supabase, y el backend es este proyecto en Django.
17. Cerrar el PR #5 de TicketJuy, que quedó reemplazado por #12.
