from django.urls import path

from . import staff_auth

urlpatterns = [
    path("login/", staff_auth.login, name="auth-login"),
    path("me/", staff_auth.me, name="auth-me"),
]
