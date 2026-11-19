from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/door/", include("catalog.door_urls")),
    path("api/v1/auth/", include("catalog.auth_urls")),
    path("api/v1/", include("catalog.urls")),
]
