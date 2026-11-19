# Panel de la plataforma

Panel web para que el equipo de la plataforma dé de alta boliches, defina sus comisiones, dé acceso a su gente y vea sus conexiones. Vive en `/plataforma/`, dentro del core (app `backoffice`), con páginas de Django y sin dependencias de frontend.

No reemplaza al `/admin/` de Django: los administradores de cada boliche siguen cargando sus fechas y entradas ahí.

## Acceso

- **Debe:** solo los superusuarios usan el panel. Sin sesión, redirige a `/plataforma/entrar/`. Con sesión de otro usuario, responde 403 con un link al `/admin/`.
- **Debe:** el 403 aplica también a los POST: un administrador de boliche no puede cambiar comisiones ni equipo.
- Las acciones que cambian datos son POST con token CSRF. Salir también es POST.

## Pantallas

| URL | Pantalla |
| --- | --- |
| `/plataforma/` | Lista de boliches |
| `/plataforma/entrar/` | Ingreso |
| `/plataforma/boliches/nuevo/` | Alta de boliche |
| `/plataforma/boliches/<slug>/` | Resumen |
| `/plataforma/boliches/<slug>/datos/` | Datos y marca |
| `/plataforma/boliches/<slug>/comision/` | Comisión e historial |
| `/plataforma/boliches/<slug>/equipo/` | Equipo y accesos |
| `/plataforma/boliches/<slug>/conexiones/` | Conexiones |
| `/plataforma/clave/<uid>/<token>/` | Creación de contraseña para un miembro nuevo |

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
- **Próximas fechas:** hasta 6, desde 12 horas antes de ahora, con estado y vendidas sobre cupo. El botón "Editar catálogo" lleva al `/admin/` filtrado por el boliche.
- **Activar o pausar:** pausar pide confirmación. **Debe:** pausar saca al boliche de la API pública al instante, y activarlo lo vuelve a mostrar.

### Datos

Los mismos campos del alta, salvo el slug.

### Comisión

- Formulario en porcentaje: quién paga el cargo de servicio, cargo al comprador (%), comisión de la plataforma (%) y motivo. **Debe:** los porcentajes van de 0 a 100, con hasta 2 decimales.
- Vista previa en el navegador sobre una entrada de $10.000: cuánto paga el comprador, cuánto recibe el boliche y cuánto retiene la plataforma. Es solo una guía: el cálculo que vale lo hace el servidor al reservar.
- Historial con los últimos 20 cambios.
- Reglas de cálculo e historial: ver [Comisiones](comisiones.md).

### Equipo

- Lista de miembros con rol y si ya crearon su contraseña.
- **Dar acceso:** email, nombre opcional y rol.
  - **Debe:** si no existe un usuario con ese email, se crea con usuario = email (en minúsculas) y sin contraseña.
  - **Debe:** si ya existe, se reutiliza. Si ya era miembro del boliche, se le cambia el rol. Su contraseña no se toca.
  - **Debe:** si el usuario no tiene contraseña, el panel muestra un link para crearla.
- **Link de acceso:** se puede volver a generar para cualquier miembro. Sirve una sola vez y vence a los 3 días (`PASSWORD_RESET_TIMEOUT` de Django). El panel no lo manda: se copia y se envía por WhatsApp.
- **Quitar:** pide confirmación y borra la membresía. **Debe:** si el usuario deja de administrar algún boliche, pierde `is_staff`.
- Sincronización de `is_staff`: ver [Multi-boliche](multi-boliche.md).

### Conexiones

Una tarjeta por proveedor con estado, cuenta vinculada, fechas y último error. El botón "Conectar" está deshabilitado hasta que exista cada integración. Ver [Conexiones](conexiones.md).

## Diseño

- Funciona en celulares desde 360 px de ancho. Las grillas pasan a una columna y las pestañas se desplazan de costado.
- Sigue el modo claro u oscuro del sistema.
- Los estilos están en `backoffice/static/backoffice/app.css`. El poco JavaScript que hay (vista previa de comisión, confirmaciones, copiar link) va dentro de cada plantilla.

## Pendiente

- Botón "Conectar Mercado Pago" con OAuth.
- Pedidos, compradores y reporte de ventas por boliche.
- Registro de quién activó o pausó un boliche.
- Probar el ancho de celular en un navegador real.
