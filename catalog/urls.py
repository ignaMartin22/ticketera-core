from django.urls import path

from . import payment_confirm, payments, views
from .gateways import views as gateway_views

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
    path(
        "venues/<slug:venue_slug>/events/<slug:event_slug>/orders/<uuid:order_id>/payment/sync/",
        payment_confirm.order_payment_sync,
        name="order-payment-sync",
    ),
    path("pagos/macro/aviso/", gateway_views.macro_notify, name="macro-notify"),
    path("pagos/macro/<uuid:order_id>/", gateway_views.macro_pay_page, name="macro-pay"),
    path("pagos/nave/<uuid:order_id>/aviso/", gateway_views.nave_notify, name="nave-notify"),
]
