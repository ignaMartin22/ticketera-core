# Specs de ticketera-core

Describen cómo funciona hoy cada parte del core. Cuando un cambio altera un comportamiento, se actualiza la spec en el mismo PR.

| Spec | Qué cubre | Estado |
| --- | --- | --- |
| [Clientes](clientes.md) | Un repo de frontend por boliche, qué vive en el core y cómo se conecta un cliente nuevo | Vigente. Credenciales por cliente pendientes |
| [Multi-boliche](multi-boliche.md) | Boliches como tenants, roles y aislamiento de datos | Implementado |
| [Catálogo y pedidos](catalogo-y-pedidos.md) | API pública, reservas de cupo e idempotencia | Implementado |
| [ABM de eventos del boliche](eventos-del-boliche.md) | Alta, edición, pausa y baja de fechas y entradas por el equipo del boliche | Implementado: API de gestión con token por usuario. Falta conectar el dashboard de cada cliente y la portada |
| [Comisiones](comisiones.md) | Cargo al comprador, comisión de la plataforma e historial | Implementado |
| [Panel de la plataforma](panel-plataforma.md) | `/plataforma/`: alta de boliches, comisión, equipo y conexiones | Implementado |
| [Conexiones](conexiones.md) | Mercado Pago, WhatsApp y email por boliche | Modelo listo, OAuth pendiente |

Lo que falta para el piloto con Infinity está en [pendientes](../pendientes.md).

## Convenciones

- **Debe** marca una regla que el código cumple y una prueba verifica. **Pendiente** marca algo decidido que todavía no está hecho.
- Los importes son pesos argentinos (ARS) y se guardan como decimales.
- Las tasas se guardan como fracción (`0.1`) y se muestran como porcentaje (10 %).
- Las fechas se guardan en UTC y se muestran en la hora de Jujuy (`America/Argentina/Jujuy`).
