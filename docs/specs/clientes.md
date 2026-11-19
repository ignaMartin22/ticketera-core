# Clientes: un repo por boliche

Cada boliche que cerramos tiene su propio repositorio de frontend. El core es el backend compartido de todos y lo administra solo el equipo de la plataforma.

```
                    ┌──────────────────────────────┐
 repo de Infinity   │ sitio público + dashboard    │──┐
 (TicketJuy)        │ del dueño (Next.js)          │  │
                    └──────────────────────────────┘  │   API
                    ┌──────────────────────────────┐  ├──────────► ticketera-core
 repo del cliente 2 │ sitio público + dashboard    │──┘            (Django + PostgreSQL)
                    └──────────────────────────────┘                    ▲
                                                                        │ /plataforma/ y /admin/
                                                              equipo de la plataforma
```

## Qué vive dónde

| | Repo del cliente | ticketera-core |
| --- | --- | --- |
| Qué es | Solo frontend | Backend compartido |
| Sitio público, catálogo y checkout | Sí | — |
| Dashboard del dueño: fechas, entradas, ventas | Sí | — |
| Marca, fotos y textos | Sí | Solo los datos que expone la API (nombre, color, rutas de logo y fotos) |
| Datos: boliches, fechas, cupos, pedidos, pagos | — | Sí |
| Comisiones, conexiones (Mercado Pago, WhatsApp, email) y equipo de cada boliche | — | Sí, desde `/plataforma/` |
| Quién entra | Compradores y el equipo del boliche | Solo el equipo de la plataforma |

- **Debe:** la gente de un boliche no tiene acceso a ninguna pantalla del core. Sus usuarios no son `is_staff`, y la migración `0005` se lo quitó a los que lo tenían.
- El repo del cliente habla con el core **desde su servidor**, nunca desde el navegador del comprador ni del dueño.
- Cada repo trabaja con un solo boliche. TicketJuy, el repo de Infinity, todavía tiene rutas `/<boliche>` de cuando iba a ser multi-boliche: con este modelo alcanza con Infinity.

## Alta de un cliente nuevo

En el core, desde `/plataforma/`:

1. **Nuevo boliche:** identificador, datos, marca y comisión. Queda pausado.
2. **Equipo:** cargar al dueño y a su gente con su rol.
3. **Conexiones:** conectar Mercado Pago (pendiente: OAuth) y, más adelante, WhatsApp y email.
4. **Activar** cuando el repo del cliente esté publicado y tenga al menos una fecha.

En el repo nuevo del cliente:

1. Crear el repo con el sitio y el dashboard. Se puede partir de TicketJuy.
2. Configurar las variables de entorno:

   | Variable | Valor |
   | --- | --- |
   | `TICKETERA_API_URL` | URL del core |
   | Identificador del boliche (`HOME_VENUE` en TicketJuy) | El identificador que se cargó en el core |
   | Credencial de la API | Pendiente: ver abajo |

3. Publicar el frontend en Railway con el dominio del cliente.

## Pendiente

- **Credenciales por cliente.** Hoy la API pública no pide credenciales. Cada repo debería tener una clave propia que el core genere y revoque desde `/plataforma/`, en la pestaña Conexiones. La clave limita a ese repo a su boliche, permite cortar el acceso de un cliente sin afectar al resto y deja registro del uso. También es lo que permitiría confiar en un encabezado con la IP del comprador: sin ella, el core solo ve la IP del servidor del cliente.
- **Login del dashboard del dueño.** El dashboard del repo del cliente necesita una API de gestión con login de los miembros del boliche: ver [ABM de eventos del boliche](eventos-del-boliche.md). El usuario y su rol ya se cargan en el core desde la pestaña Equipo. Falta cómo crea su contraseña y cómo inicia sesión, todo desde el dashboard de su sitio.
- Una plantilla de repo de cliente para no copiar TicketJuy a mano.
