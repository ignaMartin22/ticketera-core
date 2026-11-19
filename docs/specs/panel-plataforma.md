# Panel de la plataforma

Panel web para que el equipo de la plataforma dé de alta boliches, defina sus comisiones, dé acceso a su gente y vea sus conexiones. Vive en `/plataforma/`, dentro del core (app `backoffice`), con páginas de Django y sin dependencias de frontend.

Junto con el `/admin/` de Django, es la administración del core, y la usa solo el equipo de la plataforma. La gente de cada boliche trabaja desde el dashboard del repo de su boliche: ver [Clientes](clientes.md).

## Acceso

- **Debe:** solo los superusuarios usan el panel. Sin sesión, redirige a `/plataforma/entrar/`. Con sesión de otro usuario, responde 403.
- **Debe:** el 403 aplica también a los POST: un administrador de boliche no puede cambiar comisiones ni equipo.
- Las acciones que cambian datos son POST con token CSRF. Salir también es POST.

## Pantallas

| URL | Pantalla |
| --- | --- |
| `/plataforma/` | Lista de boliches |
| `/plataforma/entrar/` | Ingreso |
| `/plataforma/boliches/nuevo/` | Alta de boliche |
| `/plataforma/boliches/<slug>/` | Resumen |
| `/plataforma/boliches/<slug>/pedidos/` | Ventas y pedidos |
| `/plataforma/boliches/<slug>/datos/` | Datos y marca |
| `/plataforma/boliches/<slug>/comision/` | Comisión e historial |
| `/plataforma/boliches/<slug>/equipo/` | Equipo |
| `/plataforma/boliches/<slug>/conexiones/` | Conexiones |

### Lista de boliches

Una tarjeta por boliche con: nombre, slug, ciudad, color, estado (activo o pausado), fechas publicadas próximas, cantidad de miembros, cargo al comprador, comisión y estado de Mercado Pago.

### Alta de boliche

- Pide datos y marca (slug, nombre, ciudad, dirección, Instagram, frase, link de Maps, color, logo, portada, fondo de flyers) y la comisión.
- El slug se guarda en minúsculas y no se puede cambiar después. Instagram se guarda sin `@`.
- Por defecto propone: comprador paga 10 %, comisión de la plataforma 0 %.
- **Debe:** el boliche se crea pausado y con su primer registro en el historial de comisiones.

### Resumen

- **Para empezar a vender:** datos y marca (dirección y logo), comisión definida, administrador asignado, Mercado Pago conectado y una fecha publicada.
- **Números:** entradas vendidas, reservas vigentes, cargo al comprador y comisión.
- **Próximas fechas:** hasta 6, desde 12 horas antes de ahora, con estado y vendidas sobre cupo. El botón "Editar catálogo" lleva al `/admin/` del core filtrado por el boliche, para que la plataforma cargue fechas mientras el dashboard del boliche no esté conectado.
- **Historial de estado:** los últimos 5 cambios entre activo y pausado, con quién los hizo y cuándo.
- **Activar o pausar:** pausar pide confirmación. **Debe:** pausar saca al boliche de la API pública al instante, y activarlo lo vuelve a mostrar. **Debe:** cada cambio queda registrado con el usuario y la hora.

### Ventas

- **Ventas por fecha:** una fila por fecha del boliche, publicadas y borradores, pasadas y próximas, de la más nueva a la más vieja. Cada fila muestra vendidas, reservas vigentes, cupo, ocupación, recaudación y el detalle por tipo de entrada (vendidas sobre cupo).
- La recaudación es a precio de entrada, sin cargo de servicio ni comisión (ver [ABM de eventos](eventos-del-boliche.md#listado-y-resumen)). Pendiente: que salga de los pedidos pagados y descuente la comisión de la plataforma. Las reservas vigentes no cuentan los pedidos pagados (ya están en las vendidas).
- **Pedidos:** los últimos 100, del más nuevo al más viejo, con fecha, evento, comprador (nombre, email y teléfono), estado — pagado si tiene `paid_at`; si no, pendiente de pago o vencido según la reserva siga vigente —, cantidad de entradas y total con cargo de servicio.

### Datos

Los mismos campos del alta, salvo el slug.

### Comisión

- Formulario en porcentaje: quién paga el cargo de servicio, cargo al comprador (%), comisión de la plataforma (%) y motivo. **Debe:** los porcentajes van de 0 a 100, con hasta 2 decimales.
- Vista previa en el navegador sobre una entrada de $10.000: cuánto paga el comprador, cuánto recibe el boliche y cuánto retiene la plataforma. Es solo una guía: el cálculo que vale lo hace el servidor al reservar.
- Historial con los últimos 20 cambios.
- Reglas de cálculo e historial: ver [Comisiones](comisiones.md).

### Equipo

Quiénes administran el boliche desde el dashboard de su sitio. Cargarlos acá no les da acceso al core.

- Lista de miembros con su rol.
- **Agregar:** email, nombre opcional y rol.
  - **Debe:** si no existe un usuario con ese email, se crea con usuario = email (en minúsculas), sin contraseña y sin `is_staff`.
  - **Debe:** si ya existe (sin importar mayúsculas), se reutiliza. Si ya era miembro del boliche, se le cambia el rol.
- **Quitar:** pide confirmación y borra la membresía.
- Pendiente: la creación de la contraseña y el ingreso, desde el dashboard del boliche y con la API de gestión.

### Conexiones

Una tarjeta por proveedor con estado, cuenta vinculada, fechas y último error. Ver [Conexiones](conexiones.md).

- **Mercado Pago, sin conectar (o vencida o con error):** botón "Generar link de conexión". Al enviarlo, la misma pantalla muestra el link de autorización en un campo de solo lectura, con "Copiar link", "Abrir" y la aclaración de que hay que mandárselo al dueño, que lo abra con la cuenta donde va a cobrar y que vence en 10 minutos. El link no se guarda ni va en la URL: sale en la respuesta del POST y se muestra una sola vez.
- **Mercado Pago, conectada:** muestra la cuenta, desde cuándo y hasta cuándo, "Reconectar" y "Desconectar" (con confirmación; borra los tokens y deja la conexión en `disconnected`).
- Si faltan las credenciales de Mercado Pago en el servidor, el botón queda deshabilitado con ese aviso.
- Nunca se muestran tokens ni claves.
- WhatsApp y Email: botón "Conectar" deshabilitado hasta que exista cada integración.

## Diseño

- Funciona en celulares desde 360 px de ancho. Las grillas pasan a una columna y las pestañas se desplazan de costado.
- Sigue el modo claro u oscuro del sistema.
- Los estilos están en `backoffice/static/backoffice/app.css`. El poco JavaScript que hay (vista previa de comisión, confirmaciones, copiar link) va dentro de cada plantilla.

## Pendiente

- Probar el ancho de celular en un navegador real.
