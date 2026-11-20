from django.contrib import admin
from django.urls import include, path

from catalog.media import serve_media

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/door/", include("catalog.door_urls")),
    path("api/v1/auth/", include("catalog.auth_urls")),
    path("api/v1/manage/", include("catalog.manage_urls")),
    path("api/v1/mercadopago/", include("catalog.mp_urls")),
    path("api/v1/", include("catalog.urls")),
    path("plataforma/", include("backoffice.urls")),
    path("media/<path:path>", serve_media, name="media"),
]
