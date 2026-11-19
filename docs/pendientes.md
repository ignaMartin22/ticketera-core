# Pendientes para el piloto con Infinity

Qué falta para vender una fecha real de Infinity con TicketJuy. Estado al 19/11/2026.

## Hecho

- Catálogo multi-boliche: boliches, membresías, eventos y tipos de entrada, con admin limitado por boliche (#2).
- Pedidos sin cuenta con reserva de cupo de 10 minutos e idempotencia (#3).
- Nombre y WhatsApp del comprador en el pedido, y `manage.py seed_infinity` para desarrollo (#4).
- Entradas con QR firmado de un solo uso: se emiten al reservar un pedido gratis o al marcar un pedido como pagado en el admin.
- API de portería: login del personal de puerta, escaneo que quema la entrada y asistencia por evento. Funciona solo con conexión.
- TicketJuy lee el catálogo de esta API y el checkout reserva el cupo (TicketJuy #12). La página del pedido muestra la reserva con el botón de pago deshabilitado.

## Para vender una fecha real

1. **Mercado Pago Checkout Pro con split por boliche.** Vincular la cuenta del boliche por OAuth y renovar el token. Crear la preferencia con el token del boliche y el `marketplace_fee`. Confirmar cada pago una sola vez por webhook y conciliar los pagos sin notificación.
   - Al confirmar un pago, sumar a `sold` y dejar de contar el pedido en `TicketTypeQuerySet.with_reserved`. Si no, el cupo se cuenta dos veces hasta que vence la reserva.
   - Definir qué pasa si el pago llega después de que venció la reserva: emitir si queda cupo, o reembolsar.
   - Reembolsos.
2. **Página de la entrada.** La API ya devuelve las entradas del pedido con el texto del QR. La página `/entrada/[code]` de TicketJuy todavía usa datos de prueba.
3. **Envío de entradas** por email y WhatsApp. La API de WhatsApp Business requiere aprobación de Meta y puede tardar: conviene pedirla ya.
4. **Escáner de puerta** en el navegador del celular. La API de escaneo está hecha; falta la pantalla en TicketJuy que abre la cámara. Después, el modo sin señal con sincronización al volver, que necesita una regla de operación para cuando dos celulares sin conexión aceptan el mismo QR.
   - Límite de intentos en el login de portería.
5. **Protección contra reservas automatizadas** en `POST .../orders/` (captcha o verificación de bots y límite por IP). Hoy un bot puede bloquear cupo de a 10 entradas durante 10 minutos, una y otra vez.

## Panel y operación

6. El `/admin` de TicketJuy guarda los datos en el navegador. Para el piloto se puede usar el admin de Django, pero falta un admin de pedidos limitado por boliche: hoy no se ven los compradores.
7. Reporte de ventas, plata e ingresos por fecha.

## Puesta en producción

8. Hosting del backend con PostgreSQL administrado y backups.
   - Agregar `gunicorn` y servir los estáticos (por ejemplo con `whitenoise`).
   - Configurar `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y `SECURE_PROXY_SSL_HEADER` detrás del proxy HTTPS.
   - Correr `manage.py migrate` en cada deploy.
9. Frontend en Vercel con `TICKETERA_API_URL` apuntando al backend, y el dominio.
10. Monitoreo de errores (por ejemplo Sentry) y logs.

## Contenido y decisiones

11. Comisión y quién la paga. Hoy Infinity figura con `fee_mode=buyer` y un 10 % provisorio.
12. Fechas, precios y aforo reales, flyers, Instagram y dirección exacta del boliche.
13. Términos y condiciones, política de privacidad (se guardan nombre, teléfono y email) y política de reembolsos. Consultar con un abogado si el botón de arrepentimiento aplica a entradas con fecha.
14. Probar la compra en celulares reales, también Android de gama baja.
15. Actualizar `docs/hoja-de-ruta.md` de TicketJuy: todavía dice que la base va en Supabase, y el backend es este proyecto en Django.
16. Cerrar el PR #5 de TicketJuy, que quedó reemplazado por #12.
