from django.urls import path

from . import mp_connect

urlpatterns = [
    path("oauth/callback/", mp_connect.oauth_callback, name="mp-oauth-callback"),
]
