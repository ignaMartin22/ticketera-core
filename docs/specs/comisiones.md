# Comisiones

Cada boliche tiene su propia configuración de cobro. Hay dos conceptos separados:

| Campo de `Venue` | Qué es | Quién lo paga |
| --- | --- | --- |
| `fee_mode` | Si el cargo de servicio lo paga el comprador (`buyer`) o no se cobra (`venue`) | — |
| `buyer_fee_rate` | Cargo de servicio que se suma al precio. Solo aplica con `fee_mode = buyer` | El comprador |
| `platform_fee_rate` | Comisión que la plataforma descuenta del precio de cada entrada | El boliche |

Las dos tasas van de 0 a 1. Una restricción de la base rechaza valores fuera de rango.

## Cálculo por entrada

Para una entrada de precio `P`:

```
cargo de servicio   = redondeo(P × buyer_fee_rate, a pesos enteros)   si fee_mode = buyer, si no 0
comisión plataforma = redondeo(P × platform_fee_rate, a centavos)
paga el comprador   = P + cargo de servicio
recibe el boliche   = P − comisión plataforma
retiene la plataforma = cargo de servicio + comisión plataforma
```

El redondeo es "mitad hacia arriba" (`ROUND_HALF_UP`).

Ejemplo con P = $10.000, comprador paga 10 % y comisión 5 %: el comprador paga $11.000, el boliche recibe $9.500 y la plataforma retiene $1.500.

## Importes congelados en el pedido

- **Debe:** al reservar, cada `OrderItem` guarda `unit_price`, `unit_service_fee` y `unit_platform_fee` vigentes en ese momento.
- **Debe:** un cambio de comisión aplica solo a las reservas nuevas. Los pedidos ya hechos conservan sus importes.
- El total del pedido que ve el comprador es `Σ cantidad × (unit_price + unit_service_fee)`. La comisión de la plataforma no se muestra al comprador.

## Historial

- **Debe:** cada cambio de comisión desde el panel crea un `FeeChange` con los tres valores nuevos, quién lo hizo, cuándo y un motivo opcional.
- **Debe:** guardar sin cambios no crea un registro.
- El alta de un boliche registra su comisión inicial. La migración `0004` registró la comisión que ya tenían los boliches existentes, con el motivo "Valor anterior al historial".
- Solo los superusuarios cambian comisiones. El administrador de un boliche no ve ni edita estos campos.

## API pública

La API expone `feeMode` y `buyerFeeRate` en cada boliche para que el sitio del boliche muestre el precio final. `platform_fee_rate` no se expone.

## Mercado Pago

Implementado en `catalog/payments.py`. El link de pago es una order de Checkout Pro (API de Orders) creada con el token del boliche:

- **Debe:** `marketplace_fee = Σ cantidad × (unit_service_fee + unit_platform_fee)`, con los importes guardados en los ítems del pedido, no con las tasas actuales del boliche. Si es 0 no se manda.
- **Debe:** `Order.platform_fee` guarda ese mismo total.
- El comprador paga `Σ cantidad × (unit_price + unit_service_fee)`: la comisión de la plataforma no se suma.
- Mercado Pago deposita al boliche el total menos `marketplace_fee` y a la plataforma ese importe. Las comisiones propias de Mercado Pago se descuentan aparte y todavía hay que decidir quién las absorbe.
