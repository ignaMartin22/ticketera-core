# Conexiones

Cada boliche conecta sus propias cuentas de servicios externos. El core guarda el estado de cada conexión en `VenueIntegration`, una por boliche y proveedor.

## Proveedores

| `provider` | Para qué |
| --- | --- |
| `mercadopago` | Cobrar las entradas en la cuenta del boliche y retener la comisión de la plataforma |
| `whatsapp` | Mandar las entradas y los avisos. Requiere la aprobación de Meta para WhatsApp Business |
| `email` | Mandar las entradas por email con el nombre del boliche |

## Estado

| `status` | Significado |
| --- | --- |
| `disconnected` | Nunca se conectó o se desconectó. Es el valor por defecto |
| `connected` | La conexión funciona |
| `expired` | El token venció y no se pudo renovar. Hay que volver a conectar |
| `error` | El proveedor rechazó la conexión. El detalle está en `last_error` |

Campos de apoyo: `account_id` (id de la cuenta en el proveedor), `account_label` (nombre o email para mostrar), `connected_at`, `expires_at` y `updated_at`.

- Un boliche tiene como máximo una conexión por proveedor (restricción única `venue + provider`).
- **Debe:** el panel muestra los tres proveedores aunque no exista el registro. Uno que no existe se muestra como `disconnected` y mirarlo no crea el registro.
- Para vender, el boliche necesita Mercado Pago en `connected`. El resumen del panel lo marca como pendiente hasta entonces.

## OAuth de Mercado Pago

Implementado en `catalog/mp_connect.py`. Los tokens viven en `MercadoPagoAccount`, un modelo aparte de `VenueIntegration`, y el estado se sincroniza así:

1. El administrador del boliche pide el link de autorización por la API de gestión (`POST /api/v1/manage/venues/<boliche>/mercadopago/connect/`), con un `state` firmado que identifica al boliche y al usuario y vence a los 10 minutos.
2. El dueño del boliche abre el link, entra con su cuenta y autoriza a la plataforma.
3. Mercado Pago vuelve al callback del core (`/api/v1/mercadopago/oauth/callback/`) con un `code`. El core verifica el `state`, cambia el `code` por `access_token` y `refresh_token` y los guarda cifrados.
   - **Debe:** al conectar, `VenueIntegration` queda `connected` con `account_id` (id de la cuenta de Mercado Pago), `connected_at`, `expires_at` y `last_error` vacío. Se crea si no existía.
   - **Debe:** si el intercambio del `code` falla, queda `error` con un `last_error` corto y sin datos sensibles.
4. El token se renueva cuando faltan menos de 30 días para `expires_at` (al usarlo y con `manage.py refresh_mp_tokens`, pensado para un cron diario).
   - **Debe:** una renovación exitosa actualiza `expires_at`.
   - **Debe:** una renovación rechazada de forma definitiva, o el aviso `application.deauthorized` del webhook, pasa a `expired`. Un fallo transitorio (red, 429, 5xx) no cambia el estado.
5. Desconectar (`DELETE` en la API de gestión) borra los tokens y deja `disconnected`, sin `account_id`, `connected_at` ni `expires_at`.
6. **Debe:** consultar el estado (`GET`) no crea el registro de `VenueIntegration`.

Requisitos para los tokens:
- Se guardan cifrados, con una clave que viene de una variable de entorno (`MP_TOKEN_ENCRYPTION_KEY`) y no de la base.
- Nunca se muestran en el panel, en el admin, en la API ni en los logs.
- No viven en `VenueIntegration`: van en un modelo aparte, para que listar el estado no cargue secretos.

## Pendiente

- Botón "Conectar Mercado Pago" en la pestaña Conexiones del panel de la plataforma: hoy el link se pide desde la API de gestión.
- WhatsApp y email.
