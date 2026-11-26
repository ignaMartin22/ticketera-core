from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "backoffice"

urlpatterns = [
    path("", views.venue_list, name="venue-list"),
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("salir/", auth_views.LogoutView.as_view(next_page="backoffice:login"), name="logout"),
    path("boliches/nuevo/", views.venue_create, name="venue-create"),
    path("boliches/<slug:slug>/", views.venue_detail, name="venue-detail"),
    path("boliches/<slug:slug>/estado/", views.venue_toggle, name="venue-toggle"),
    path("boliches/<slug:slug>/pedidos/", views.venue_orders, name="venue-orders"),
    path("boliches/<slug:slug>/datos/", views.venue_edit, name="venue-edit"),
    path("boliches/<slug:slug>/comision/", views.venue_fees, name="venue-fees"),
    path("boliches/<slug:slug>/equipo/", views.venue_team, name="venue-team"),
    path("boliches/<slug:slug>/equipo/<int:pk>/quitar/", views.member_remove, name="member-remove"),
    path("boliches/<slug:slug>/conexiones/", views.venue_integrations, name="venue-integrations"),
    path("boliches/<slug:slug>/conexiones/mercadopago/link/", views.venue_mp_connect, name="venue-mp-connect"),
    path("boliches/<slug:slug>/conexiones/mercadopago/desconectar/", views.venue_mp_disconnect,
         name="venue-mp-disconnect"),
    path("boliches/<slug:slug>/conexiones/<str:provider>/credenciales/", views.venue_gateway_save,
         name="venue-gateway-save"),
    path("boliches/<slug:slug>/conexiones/<str:provider>/desconectar/", views.venue_gateway_disconnect,
         name="venue-gateway-disconnect"),
]
