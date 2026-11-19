from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "backoffice"

urlpatterns = [
    path("", views.venue_list, name="venue-list"),
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("salir/", auth_views.LogoutView.as_view(next_page="backoffice:login"), name="logout"),
    path("clave/listo/", views.set_password_done, name="set-password-done"),
    path("clave/<uidb64>/<token>/", views.SetPasswordView.as_view(), name="set-password"),
    path("boliches/nuevo/", views.venue_create, name="venue-create"),
    path("boliches/<slug:slug>/", views.venue_detail, name="venue-detail"),
    path("boliches/<slug:slug>/estado/", views.venue_toggle, name="venue-toggle"),
    path("boliches/<slug:slug>/datos/", views.venue_edit, name="venue-edit"),
    path("boliches/<slug:slug>/comision/", views.venue_fees, name="venue-fees"),
    path("boliches/<slug:slug>/equipo/", views.venue_team, name="venue-team"),
    path("boliches/<slug:slug>/equipo/<int:pk>/link/", views.member_link, name="member-link"),
    path("boliches/<slug:slug>/equipo/<int:pk>/quitar/", views.member_remove, name="member-remove"),
    path("boliches/<slug:slug>/conexiones/", views.venue_integrations, name="venue-integrations"),
]
