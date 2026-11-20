from django.urls import path

from . import guest_lists

urlpatterns = [
    path("lists/", guest_lists.rrpp_lists, name="rrpp-lists"),
    path("lists/<int:list_id>/", guest_lists.rrpp_list_detail, name="rrpp-list-detail"),
    path("lists/<int:list_id>/guests/", guest_lists.rrpp_guest_create, name="rrpp-guest-create"),
]
