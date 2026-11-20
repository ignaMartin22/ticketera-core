from django.urls import path

from . import manage, mp_connect

urlpatterns = [
    path("venues/<slug:venue_slug>/mercadopago/connect/", mp_connect.connect, name="mp-connect"),
    path("venues/<slug:venue_slug>/mercadopago/", mp_connect.account_status, name="mp-status"),
    path("venues/<slug:venue_slug>/events/", manage.event_collection, name="manage-event-list"),
    path(
        "venues/<slug:venue_slug>/events/<slug:event_slug>/cover/",
        manage.event_cover,
        name="manage-event-cover",
    ),
    path(
        "venues/<slug:venue_slug>/events/<slug:event_slug>/",
        manage.event_resource,
        name="manage-event-detail",
    ),
]
