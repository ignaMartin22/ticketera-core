# Pasarelas sin reparto: Macro Click de Pago y Nave

Además de Mercado Pago, un boliche puede cobrar con **Macro Click de Pago** (botón de pago de Banco Macro, sobre la plataforma PlusPagos) y con **Nave** (la plataforma de cobros de Naranja X y Galicia). El comprador elige el medio en la página del pedido, entre los que el boliche tenga conectados.

Implementado en `catalog/gateways/`. **Ninguna de las dos API está probada contra el banco**: el formato sale de integraciones de terceros (ver [Fuentes](#fuentes)). Antes de vender hay que probar con las credenciales de prueba y la documentación oficial (ver [Pendiente](#pendiente)).

## Sin reparto

Ninguna de las dos pasarelas separa la comisión en el mismo pago, como hace `marketplace_fee` en Mercado Pago:

- Toda la venta entra a la cuenta del boliche.
- **Debe:** al crear el cobro, `Order.platform_fee` guarda el cargo de servicio más la comisión de la plataforma, con los importes congelados en el pedido. Es el mismo cálculo que el de Mercado Pago (ver [comisiones](comisiones.md)), y se liquida por fuera, como el cobro directo.
- Si el boliche no paga comisión (por ejemplo, porque compró el sistema), su `platform_fee_rate` va en 0.

## Credenciales

- Las entrega el banco y las carga un superusuario en la pestaña Conexiones del panel (`/plataforma/boliches/<slug>/conexiones/`):
  - **Macro:** ID de comercio, clave secreta (frase) e ID de sucursal (opcional).
  - **Nave:** client ID, client secret y POS ID.
  - En las dos, si son de producción o del entorno de prueba del banco.
- **Debe:** se guardan en `GatewayAccount.credentials` como un JSON cifrado con `MP_TOKEN_ENCRYPTION_KEY`, la misma clave que los tokens de Mercado Pago.
- **Debe:** las claves nunca se muestran en el panel, en la API ni en los logs. Para cambiarlas se cargan de nuevo.
- **Debe:** al guardarlas, `VenueIntegration` queda `connected`, con el ID de comercio o el POS ID en `account_id` y el entorno en `account_label`. Desconectar borra las credenciales y deja `disconnected`.
- Hacen falta `PUBLIC_API_URL` (los avisos y la página de Macro viven en el core) y la clave de cifrado. Sin ellas el panel no deja cargar credenciales y el link de pago responde `503 gateway_not_configured`.

## Link de pago

`POST /api/v1/venues/<boliche>/events/<evento>/orders/<pedido>/payment/` acepta un cuerpo JSON `{"provider": "mercadopago" | "macro" | "nave"}`.

- Sin cuerpo JSON, es Mercado Pago, como antes.
- Responde `{"initPoint": "<url>", "provider": "<medio>"}`.
- **Debe:** se valida el pedido igual que con Mercado Pago: existe y es de ese boliche y evento, no está pagado (`409 order_already_paid`) y no venció (`409 order_expired`).
- **Debe:** un medio que no existe responde `400 invalid_provider`. Si el boliche no lo tiene conectado, `409 venue_not_connected`. Si la pasarela falla, `502 gateway_unavailable`.
- **Debe:** es idempotente: un pedido tiene como máximo un `GatewayCharge` por pasarela y un segundo pedido de link devuelve el mismo.
- La API del pedido devuelve `paymentMethods` con los medios disponibles, en orden: `mercadopago` (si hay una cuenta que no necesita reconexión), `macro` y `nave`. Sin reserva vigente, la lista va vacía.

### Macro

El botón de Macro no es una API que devuelve una URL: el navegador tiene que mandarle un formulario con los datos cifrados.

- La referencia del cobro (`TransaccionComercioId`) es el id del pedido sin guiones.
- El link es una página del core, `GET /api/v1/pagos/macro/<pedido>/`, que arma el formulario y lo envía sola al botón del entorno que corresponda (`MACRO_CLICK_URL` o `MACRO_CLICK_TEST_URL`).
- **Debe:** el monto, la sucursal y las URL de vuelta van cifrados con AES-256-CBC usando la frase del comercio, con un vector inicial al azar delante y en base64. La clave es la frase repetida o cortada a 32 bytes. El monto va en centavos, sin separadores: $11.000 es `1100000`.
- **Debe:** la firma (`Hash`) es el SHA-256 en hexadecimal de `IP*comercio*sucursal*monto*frase`, con la IP de quien abre la página. Detrás del proxy (`DJANGO_BEHIND_PROXY=1`), es la primera de `X-Forwarded-For`.
- La página no muestra la frase, no se indexa y no manda referrer. Si el pedido ya está pagado o venció, redirige a la página del pedido del front.

### Nave

- Se pide un token OAuth entre servidores (`NAVE_AUTH_URL` o `NAVE_AUTH_TEST_URL`) con el client ID y el client secret. Se guarda en la caché hasta un minuto antes de vencer.
- Se crea la intención de pago (`POST /payment_request/ecommerce`) con:
  - el id del pedido como `external_payment_id`;
  - el POS ID;
  - el total y las entradas, con el cargo de servicio incluido;
  - el email, el nombre y el teléfono del comprador;
  - la URL de vuelta y la de aviso;
  - lo que le queda a la reserva, con un mínimo de 60 segundos.
- **Debe:** el `checkout_url` que devuelve Nave tiene que ser https y de un dominio de `NAVE_CHECKOUT_HOSTS`. Si no, no se guarda el cobro y se responde `502`.

## Confirmación

Las mismas reglas que con Mercado Pago (ver `payment_confirm`):

- **Debe:** nunca se confía en el aviso. Siempre se consulta el cobro en la pasarela con las credenciales del boliche.
- **Debe:** un cobro emite las entradas una sola vez.
- **Debe:** lo que no se puede emitir queda "a revisar" y el pedido marca `paymentReview`:
  - monto o moneda distintos del total (`mismatch`);
  - pedido ya pagado por otro cobro (`duplicate`);
  - reserva vencida y sin cupo (`no_capacity`).
- La decisión es la misma función para las tres pasarelas (`payment_confirm.approved_outcome`).

### Macro

- El aviso (`POST /api/v1/pagos/macro/aviso/`, que hay que cargar en el panel de Macro) no viene firmado. Solo dispara la consulta.
- **Debe:** se consulta la API de PlusPagos (`MACRO_API_URL`):
  - se inicia sesión con el ID de comercio como `guid` y la frase como `frase`;
  - se buscan las transacciones del día del cobro con un margen de un día, filtradas por sucursal si hay;
  - el cobro está aprobado si su transacción tiene `estado` 3, y se compara su `montoBruto`.
- **Debe:** un aviso de una referencia que no es nuestra se responde 200 y no se toca nada. Si la consulta falla, se responde 500 para que Macro reintente.
- El pago por DEBIN queda pendiente hasta que el comprador lo aprueba en su banco. Si eso pasa después de que vence la reserva, el cobro se emite solo si sigue habiendo cupo; si no, queda `no_capacity`.

### Nave

- La URL de aviso es `/api/v1/pagos/nave/<pedido>/aviso/?secret=<secreto>`, con un secreto al azar por cobro (`GatewayCharge.notify_secret`).
- **Debe:** sin el secreto correcto, responde `401`.
- **Debe:** se consulta la intención de pago (`GET /payment_requests/<id>`). Está aprobada si su estado es `SUCCESS_PROCESSED`, su `external_payment_id` es el pedido y la moneda es ARS.

### Sin aviso

- `POST .../payment/sync/` (la página del pedido lo llama mientras espera) también consulta los cobros de pasarela del pedido.
- `manage.py reconcile_mp_payments` (cron) revisa los pedidos sin pagar de las últimas 48 horas que tengan un link de Mercado Pago o un cobro de pasarela.

## Variables

Todas son opcionales y traen la dirección conocida de cada entorno; están por si el banco las cambia:

| Variable | Para qué |
| --- | --- |
| `MACRO_CLICK_URL`, `MACRO_CLICK_TEST_URL` | Botón de pago de Macro, en producción y en prueba |
| `MACRO_API_URL`, `MACRO_API_TEST_URL` | API de PlusPagos para consultar transacciones |
| `NAVE_AUTH_URL`, `NAVE_AUTH_TEST_URL` | Token OAuth de Nave |
| `NAVE_API_URL`, `NAVE_API_TEST_URL` | API de Nave |
| `NAVE_AUDIENCE` | Audiencia del token de Nave |
| `NAVE_CHECKOUT_HOSTS` | Dominios permitidos para el checkout de Nave, separados por coma |

## Pendiente

- **Probar con las credenciales de prueba de cada banco.** Para conseguirlas:
  - **Macro:** el boliche necesita cuenta en Macro. Pide a su oficial de cuentas el alta del "Botón Integrado" de Click de Pago; el banco le manda por mail el ID de comercio y la clave secreta, de prueba y de producción.
  - **Nave:** el boliche necesita cuenta de comercio en Naranja X o en Galicia. En Nave, en Integraciones › Tienda online propia, activa el código de vinculación y lo manda con su CUIT a integraciones@navenegocios.com, pidiendo el alta de "Online · Checkout". Nave devuelve el client ID, el client secret y el POS ID.
- **Confirmar con la documentación de Macro:**
  - el formato del formulario y de la firma, sobre todo qué IP espera;
  - cómo se verifica un aviso, y si la API de consulta usa el ID de comercio y la frase o credenciales aparte;
  - los estados de una transacción y el formato de los montos.
- **Confirmar con Nave:**
  - si hace falta el bloque `platform` del pedido (el plugin oficial manda uno);
  - el dominio exacto del checkout;
  - los estados de la intención de pago.
- **Reporte de liquidación** por boliche y período con `Order.platform_fee` de lo cobrado por Macro y Nave, si el boliche paga comisión.
- Reembolsos: como con Mercado Pago, no hay. Lo que queda a revisar lo resuelve el boliche a mano.

## Fuentes

- Macro: [plugin no oficial para WooCommerce](https://github.com/lucianokatze/macroclickdepago-for-woocommerce) (formulario, cifrado, firma y aviso) y [script de la API de PlusPagos](https://github.com/akalautaro/transacciones_billetera_santafe) (sesión y consulta de transacciones).
- Nave: [plugin oficial para WooCommerce](https://wordpress.org/plugins/nave-for-woocommerce/) (token, intención de pago, aviso y estados) y [SDK no oficial para Node](https://github.com/emilioastarita/sdk-nave-nodejs).
