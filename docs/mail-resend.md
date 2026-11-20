# Mail de entradas con Resend

Cuando se emiten las entradas de un pedido (pago confirmado, pedido gratis o "Marcar como pagado" en el admin), el core le manda al comprador un mail con un QR por entrada y el link a su pedido. El envío usa SMTP, así que sirve cualquier proveedor. Esta guía usa [Resend](https://resend.com).

## Cómo se comporta

- El mail sale después de guardar las entradas. Si falla, el pago y las entradas no se tocan: el pedido queda con `tickets_emailed_at` vacío.
- `manage.py send_ticket_emails` reintenta los pedidos pagados sin mail, desde los 5 minutos y hasta los 2 días después del pago. Va en el cron de la VPS cada 10 minutos ([despliegue](despliegue-vps.md), paso 8).
- Pasados los 2 días, o para un comprador que no lo encuentra: admin de Django → Pedidos → "Reenviar las entradas por email".
- Los errores quedan en el log de `web` con el texto "mail de entradas".

## Etapa 1: probar sin dominio propio

Resend deja mandar sin dominio verificado, pero **solo desde `onboarding@resend.dev` y solo al mail de la cuenta de Resend**. A cualquier otro comprador el envío falla (queda en el log y se reintenta hasta los 2 días).

1. Crear la cuenta en resend.com con una casilla del equipo y activar la verificación en dos pasos.
2. **API Keys → Create API Key**, permiso **Sending access**. Copiarla: empieza con `re_` y se ve una sola vez.
3. En `deploy/.env` de la VPS:

   ```
   DJANGO_EMAIL_HOST=smtp.resend.com
   DJANGO_EMAIL_PORT=587
   DJANGO_EMAIL_HOST_USER=resend
   DJANGO_EMAIL_HOST_PASSWORD=re_xxxxxxxxxxxxxxxx
   DJANGO_EMAIL_USE_TLS=1
   DJANGO_DEFAULT_FROM_EMAIL=Infinity Entradas <onboarding@resend.dev>
   ```

   El usuario es literalmente `resend` y la contraseña es la API key.
4. Recrear `web`: `docker compose -f deploy/docker-compose.yml up -d web` (con `--build` si cambió el código).
5. Probar el SMTP: `docker compose -f deploy/docker-compose.yml exec web python manage.py sendtestemail <mail de la cuenta de Resend>`.
6. Hacer una compra de prueba con el mail de la cuenta de Resend en el checkout: tiene que llegar el mail con los QR. Escanear uno desde `/puerta` para cerrar el circuito.

## Etapa 2: dominio propio

Un dominio de Railway (`*.up.railway.app`) o de sslip.io no sirve: hay que poder cargar registros DNS.

1. **Comprar el dominio** de la plataforma. Un `.com.ar` se registra en NIC Argentina (el titular necesita CUIT o CUIL); un `.com`, en Cloudflare, Namecheap o Porkbun. Para el DNS conviene Cloudflare (gratis).
2. **Verificarlo en Resend** con un subdominio solo para mails, por ejemplo `mail.<dominio>`. Separa la reputación de envío del dominio principal y no choca con su correo. En **Domains → Add Domain**, región São Paulo.
3. **Cargar en el DNS** los registros que muestra Resend, con los valores exactos del panel:

   | Nombre | Tipo | Para qué |
   | --- | --- | --- |
   | `send.mail` | MX y TXT | Rebotes y SPF |
   | `resend._domainkey.mail` | TXT | DKIM |
   | `_dmarc.mail` | TXT `v=DMARC1; p=none;` | DMARC (recomendado) |

4. Esperar a que figure **Verified** y cambiar el remitente en `deploy/.env`. El remitente es de la plataforma; el nombre visible, del boliche:

   ```
   DJANGO_DEFAULT_FROM_EMAIL=Infinity Entradas <entradas@mail.<dominio>>
   DJANGO_TICKETS_REPLY_TO=<casilla que alguien lea>
   ```

   Sin `DJANGO_TICKETS_REPLY_TO`, las respuestas de los compradores van al remitente, que no es un buzón real.
5. Recrear `web` y repetir la compra de prueba con un mail que no sea el de la cuenta.

Con el dominio también se pueden mover el front (`entradas.<dominio>` en Railway) y la API (`api.<dominio>` en la VPS).

## Si no llega

- **Resend → Emails**: muestra si se entregó, rebotó o fue a spam.
- `docker compose -f deploy/docker-compose.yml logs web | grep "mail de entradas"`: el error del envío.
- Error de autenticación: la API key. "Not authorized to send" o similar: el dominio no está verificado o el remitente no es de ese dominio.
