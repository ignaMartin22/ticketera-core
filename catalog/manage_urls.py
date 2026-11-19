from django.urls import path

from . import mp_connect

urlpatterns = [
    path("venues/<slug:venue_slug>/mercadopago/connect/", mp_connect.connect, name="mp-connect"),
    path("venues/<slug:venue_slug>/mercadopago/", mp_connect.account_status, name="mp-status"),
]
