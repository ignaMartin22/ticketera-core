from django.urls import path

from . import mp_connect, payment_confirm

urlpatterns = [
    path("webhook/", payment_confirm.webhook, name="mp-webhook"),
    path("oauth/callback/", mp_connect.oauth_callback, name="mp-oauth-callback"),
]
