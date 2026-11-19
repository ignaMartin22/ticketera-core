from django.urls import path

from . import views

urlpatterns = [
    path("venues/", views.venue_list, name="venue-list"),
    path("venues/<slug:venue_slug>/events/", views.event_list, name="event-list"),
    path("venues/<slug:venue_slug>/events/<slug:event_slug>/", views.event_detail, name="event-detail"),
]
