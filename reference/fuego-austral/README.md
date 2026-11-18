# Referencia de Fuego Austral

Selección de código de [fuegoaustral/ticketera](https://github.com/fuegoaustral/ticketera/tree/466a9397584890e1b7eef159432ed4bbe2e0f0e0), importada con autorización de sus responsables. Los archivos conservan su contenido y rutas relativas al proyecto de origen.

| Flujo | Archivos |
| --- | --- |
| Eventos, cupos y órdenes | [Eventos](events/models.py), [modelos de entradas y órdenes](tickets/models.py), [reglas de negocio](docs/reglas-de-negocio.md) |
| Compra y emisión | [Checkout](tickets/views/checkout.py), [emisión](tickets/processing.py), [ciclo de órdenes y pagos](docs/ordenes-y-pagos.md) |
| Confirmación de pagos | [Webhook](tickets/views/webhooks.py), [conciliación](tickets/payment_check_cron.py) |
| Ventas en caja | [Modelos](caja/models.py), [servicio de ventas](caja/services/sales.py), [documentación de caja](docs/caja-v2.md) |

Estos archivos son una referencia para la implementación. Dependen de modelos, configuración y servicios del Django de Fuego Austral. Para consultar dependencias o enlaces internos fuera de esta selección, usar el árbol de origen enlazado arriba.

La integración con TicketJuy empieza por el catálogo y el alcance de datos por boliche. Para adaptar el flujo de pagos, hay que resolver la cuenta de Mercado Pago por boliche, comprobar el importe y la moneda de cada orden y hacer idempotente la transición de pago a emisión.
