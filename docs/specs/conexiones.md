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

## Pendiente: OAuth de Mercado Pago

1. En la pestaña Conexiones, "Conectar Mercado Pago" genera el link de autorización con un `state` firmado que identifica al boliche.
2. El dueño del boliche abre el link, entra con su cuenta y autoriza a la plataforma.
3. Mercado Pago vuelve al callback del core con un `code`. El core verifica el `state`, cambia el `code` por `access_token` y `refresh_token` y guarda `account_id`, `account_label`, `connected_at`, `expires_at` y `status = connected`.
4. Una tarea renueva el token antes de `expires_at`. Si la renovación falla, pasa a `expired` y el panel lo muestra.
5. Desconectar borra los tokens y vuelve a `disconnected`.

Requisitos para los tokens:
- Se guardan cifrados, con una clave que viene de una variable de entorno y no de la base.
- Nunca se muestran en el panel, en el admin, en la API ni en los logs.
- No viven en `VenueIntegration`: van en un modelo aparte, para que listar el estado no cargue secretos.
