from django.urls import path

from . import door

urlpatterns = [
    path("login/", door.login, name="door-login"),
    path("venues/<slug:venue_slug>/events/", door.event_list, name="door-event-list"),
    path("venues/<slug:venue_slug>/events/<slug:event_slug>/scan/", door.scan, name="door-scan"),
]
