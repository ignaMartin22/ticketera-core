from django.urls import path

from . import payments, views

urlpatterns = [
    path("venues/", views.venue_list, name="venue-list"),
    path("venues/<slug:venue_slug>/events/", views.event_list, name="event-list"),
    path("venues/<slug:venue_slug>/events/<slug:event_slug>/", views.event_detail, name="event-detail"),
    path(
        "venues/<slug:venue_slug>/events/<slug:event_slug>/orders/",
        views.order_create,
        name="order-create",
    ),
    path(
        "venues/<slug:venue_slug>/events/<slug:event_slug>/orders/<uuid:order_id>/",
        views.order_detail,
        name="order-detail",
    ),
    path(
        "venues/<slug:venue_slug>/events/<slug:event_slug>/orders/<uuid:order_id>/payment/",
        payments.order_payment,
        name="order-payment",
    ),
]
