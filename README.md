# Ticketera Core

Backend de TicketJuy para operar varios boliches desde una misma plataforma. Moon es el primer cliente. El frontend y el alcance del producto viven en [TicketJuy](https://github.com/ignaMartin22/TicketJuy).

## Flujos de Fuego Austral que sirven de referencia

Los archivos seleccionados están disponibles en [la referencia local](reference/fuego-austral/README.md).

| Flujo | Referencia | Adaptación para TicketJuy |
| --- | --- | --- |
| Eventos y cupos | [Eventos](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/events/models.py) y [tipos de entrada](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/models.py) | Asociar cada evento a un boliche. Controlar cupos por tipo y reservarlos en una transacción. |
| Compra y emisión | [Checkout](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/views/checkout.py) y [emisión de entradas](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/processing.py) | Permitir compras sin cuenta y emitir una entrada por unidad pagada. |
| Pagos | [Webhook de Mercado Pago](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/views/webhooks.py) y [conciliación](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/tickets/payment_check_cron.py) | Vincular la cuenta del boliche por OAuth. Crear el pago con el token del vendedor y guardar la comisión de la plataforma por orden. Confirmar cada pago una sola vez. |
| Caja y puerta | [Flujo de caja](https://github.com/fuegoaustral/ticketera/blob/466a9397584890e1b7eef159432ed4bbe2e0f0e0/caja/services/sales.py) | En caja, registrar canal y operador y usar el mismo stock que la venta online. En puerta, validar un QR firmado y registrar el ingreso. |

## Orden de implementación

1. Boliches, membresías del personal y catálogo de eventos y tipos de entrada. El frontend puede reemplazar sus datos de prueba una vez disponible el catálogo.
2. Órdenes de invitados y reservas de cupo con vencimiento.
3. Checkout Pro con split por boliche, confirmación idempotente y emisión de entradas.
4. Escaneo en puerta con sincronización offline y panel de ventas y asistencia. Si dos dispositivos están desconectados, pueden aceptar el mismo QR antes de sincronizar. El piloto necesita una regla de operación para resolver ese caso.
5. Caja, RRPP, listas y mesas VIP después del MVP.

El aislamiento de datos por boliche aplica desde la primera entrega. El piloto usa Moon, y las pruebas deben incluir un segundo boliche para detectar accesos cruzados.
