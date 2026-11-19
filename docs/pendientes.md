# Pendientes para el piloto con Infinity

Qué falta para vender una fecha real de Infinity con TicketJuy. Estado al 19/11/2026.

## Hecho

- Catálogo multi-boliche: boliches, membresías, eventos y tipos de entrada, con admin limitado por boliche (#2).
- Pedidos sin cuenta con reserva de cupo de 10 minutos e idempotencia (#3).
- Nombre y WhatsApp del comprador en el pedido, y `manage.py seed_infinity` para desarrollo (#4).
- Panel de la plataforma en `/plataforma/`, solo para el equipo de la plataforma: alta y pausa de boliches, comisión con historial (cargo al comprador y comisión de la plataforma), equipo de cada boliche y estado de las conexiones.
- La gente de los boliches no entra al core: cada cliente tiene su repo con su sitio y su dashboard. Ver las [specs](specs/README.md).
- TicketJuy lee el catálogo de esta API y el checkout reserva el cupo (TicketJuy #12). La página del pedido muestra la reserva con el botón de pago deshabilitado.

## Para vender una fecha real

1. **Mercado Pago Checkout Pro con split por boliche.** Vincular la cuenta del boliche por OAuth y renovar el token, según la [spec de conexiones](specs/conexiones.md). El modelo de estado y la pestaña del panel ya existen. Crear la preferencia con el token del boliche y el `marketplace_fee` (ver [comisiones](specs/comisiones.md)). Decidir quién absorbe las comisiones de Mercado Pago. Confirmar cada pago una sola vez por webhook y conciliar los pagos sin notificación.
   - Al confirmar un pago, sumar a `sold` y dejar de contar el pedido en `TicketTypeQuerySet.with_reserved`. Si no, el cupo se cuenta dos veces hasta que vence la reserva.
   - Definir qué pasa si el pago llega después de que venció la reserva: emitir si queda cupo, o reembolsar.
   - Reembolsos.
2. **Emisión de entradas.** Modelo `Ticket` con un QR firmado de un solo uso por cada unidad pagada. La página `/entrada/[code]` de TicketJuy todavía usa datos de prueba.
3. **Envío de entradas** por email y WhatsApp. La API de WhatsApp Business requiere aprobación de Meta y puede tardar: conviene pedirla ya.
4. **Escáner de puerta** en el navegador del celular, que funcione sin señal y sincronice al volver. Falta una regla de operación para cuando dos celulares sin conexión aceptan el mismo QR.
5. **Protección contra reservas automatizadas** en `POST .../orders/` (captcha o verificación de bots y límite por IP). Hoy un bot puede bloquear cupo de a 10 entradas durante 10 minutos, una y otra vez.

## Panel y operación

6. Para el piloto, cada boliche carga su catálogo en el admin de Django y la plataforma lo administra desde `/plataforma/`. Falta ver los pedidos y compradores por boliche en el panel.
7. Reporte de ventas, plata e ingresos por fecha.
8. Conectar el dashboard `/admin` de TicketJuy (el repo de Infinity) al core según la [spec del ABM de eventos](specs/eventos-del-boliche.md). Hoy lo que carga el dueño queda en su navegador. La API de gestión ya existe; falta en el core:
   - Portada de la fecha.
   - Login del dueño desde su dashboard: hoy el token por usuario lo genera la plataforma con `manage.py create_api_token`.
   - Una credencial por repo de cliente, generada desde `/plataforma/` ([Clientes](specs/clientes.md)).

## Puesta en producción

9. Hosting del backend con PostgreSQL administrado y backups.
   - Agregar `gunicorn` y servir los estáticos (por ejemplo con `whitenoise`).
   - Configurar `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y `SECURE_PROXY_SSL_HEADER` detrás del proxy HTTPS.
   - Correr `manage.py migrate` en cada deploy.
10. Frontend en Vercel con `TICKETERA_API_URL` apuntando al backend, y el dominio.
11. Monitoreo de errores (por ejemplo Sentry) y logs.

## Contenido y decisiones

12. Acordar la comisión con Infinity y cargarla en el panel. Hoy figura con un 10 % provisorio a cargo del comprador y 0 % de comisión de la plataforma.
13. Fechas, precios y aforo reales, flyers, Instagram y dirección exacta del boliche.
14. Términos y condiciones, política de privacidad (se guardan nombre, teléfono y email) y política de reembolsos. Consultar con un abogado si el botón de arrepentimiento aplica a entradas con fecha.
15. Probar la compra en celulares reales, también Android de gama baja.
16. Actualizar `docs/hoja-de-ruta.md` de TicketJuy: todavía dice que la base va en Supabase, y el backend es este proyecto en Django.
17. Cerrar el PR #5 de TicketJuy, que quedó reemplazado por #12.
