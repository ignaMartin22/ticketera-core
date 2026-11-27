# Mudar el backend a otro servidor

Qué hay que llevarse de la VPS de pruebas (`/opt/ticketjuy`, `https://api.66-97-35-67.sslip.io`) y qué hay que reconfigurar afuera para que, en el servidor nuevo, sigan valiendo las entradas ya vendidas, las cuentas de cobro conectadas y los avisos de pago.

Los pasos de instalación son los de [despliegue-vps.md](despliegue-vps.md); este documento suma lo que cambia cuando ya hay datos y plata real.

## 1. Lo que no se puede perder

| Qué | Dónde está hoy | Qué pasa si se pierde o cambia |
|---|---|---|
| Base de datos | volumen `pgdata` (`deploy/backup.sh` → `backups/ticketjuy-*.sql.gz`) | Se pierden pedidos, entradas, boliches, usuarios y cuentas de cobro. |
| Portadas de las fechas | volumen `media` (`backups/ticketjuy-media-*.tar.gz`) | Las fechas quedan sin flyer. |
| `DJANGO_SECRET_KEY` | `deploy/.env` | Los QR ya enviados dejan de valer si no hay `TICKET_QR_KEY` con `TICKET_QR_ACCEPT_LEGACY=1`, y todos los usuarios tienen que volver a iniciar sesión. **Copiar el mismo valor.** |
| `MP_TOKEN_ENCRYPTION_KEY` | `deploy/.env` | No se pueden descifrar los tokens de Mercado Pago ni las credenciales de Macro y Nave guardadas: cada boliche tiene que volver a conectar. **Copiar el mismo valor.** |
| `TICKET_QR_KEY` | `deploy/.env` | Los QR firmados con ella dejan de valer. **Copiar el mismo valor.** |
| `MP_WEBHOOK_SECRET` | `deploy/.env` (sale del panel de Mercado Pago) | El webhook responde `invalid_signature`; los pagos se confirman igual cuando el comprador vuelve al pedido. |
| `FRONT_API_KEY` | `deploy/.env` y Railway (`TICKETERA_FRONT_KEY`) | Si no coinciden, el front no puede crear pedidos (403). Copiar la misma o cambiarla en los dos lados a la vez. |
| SMTP (`DJANGO_EMAIL_*`, `DJANGO_EMAIL_FALLBACK_*`) | `deploy/.env` | No salen los mails de entradas (quedan pendientes y se reintentan). |

El resto de `deploy/.env` (`POSTGRES_PASSWORD`, `MP_APP_ID`, `MP_CLIENT_SECRET`, topes) se puede copiar tal cual.

## 2. Lo que apunta a la dirección vieja

Si el backend cambia de dominio, hay que actualizar todo esto. Si el dominio nuevo apunta al servidor nuevo y se conserva el mismo nombre, no hace falta tocar nada de esta lista salvo el DNS.

En `deploy/.env` del servidor nuevo:

- `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` (con `https://`).
- `PUBLIC_API_URL`: la usan la página de pago de Macro, los avisos de Nave y las portadas.
- `MP_REDIRECT_URI`: `https://<dominio>/api/v1/mercadopago/oauth/callback/`.
- `FRONT_BASE_URL`: solo si también se muda el front.

Afuera:

- **Front (Railway)**: `TICKETERA_API_URL=https://<dominio>` y redesplegar.
- **Mercado Pago, panel de la aplicación**:
  - Webhooks, en modo productivo: `https://<dominio>/api/v1/mercadopago/webhook/`, evento de órdenes. La clave de firma no cambia.
  - URL de redirección de OAuth: la misma que `MP_REDIRECT_URI`.
- **Macro y Nave**: no se configuran en sus paneles. Las URLs de aviso y de la página de pago se arman con `PUBLIC_API_URL` en cada cobro. Los cobros iniciados antes de la mudanza traen la dirección vieja: conviene mudar sin pedidos a mitad de pago o dejar la VPS vieja respondiendo un rato.
- **Pedidos viejos de Mercado Pago**: su URL de retorno apunta al front, no al backend, así que no se rompen.

## 3. Orden sugerido

1. En el servidor nuevo: instalar según [despliegue-vps.md](despliegue-vps.md) con el `deploy/.env` copiado (sección 1) y las direcciones nuevas (sección 2). No correr `seed_infinity`.
2. Restaurar la base y las portadas del último backup y levantar. Verificar con el `curl` del paso 7 de despliegue-vps.md, apuntando al dominio nuevo.
3. Cortar ventas en la vieja: desde el panel del boliche, pausar las ventas de las fechas abiertas (`salesOpen: false`), o elegir un horario sin ventas.
4. Backup final en la vieja (`deploy/backup.sh`), restaurarlo en la nueva.
5. Cambiar `TICKETERA_API_URL` en Railway y el webhook y la redirección en Mercado Pago (sección 2). Si cambia el dominio, cambiar el DNS.
6. Reabrir ventas y probar: escanear una entrada vieja en `/puerta`, crear un pedido y pagarlo, y revisar que llegue el mail.
7. Dejar la VPS vieja apagada pero sin borrar unos días, con su backup. Para desinstalarla: paso 10 de despliegue-vps.md.
8. Cron en el servidor nuevo: backup diario, `refresh_mp_tokens` y reintento de mails (paso 8 de despliegue-vps.md).
