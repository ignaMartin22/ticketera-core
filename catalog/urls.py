from django.urls import path

from . import manage, views

urlpatterns = [
    path(
        "manage/venues/<slug:venue_slug>/events/",
        manage.event_collection,
        name="manage-event-list",
    ),
    path(
        "manage/venues/<slug:venue_slug>/events/<slug:event_slug>/",
        manage.event_resource,
        name="manage-event-detail",
    ),
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
]
