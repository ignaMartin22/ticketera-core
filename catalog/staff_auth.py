"""Autenticación del personal de los boliches: token firmado, login y permisos por boliche."""

import hashlib
import hmac
import json
from datetime import timedelta
from functools import wraps

from django.contrib.auth import authenticate, get_user_model
from django.core import signing
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .client import buyer_ip
from .models import ApiToken, DoorLoginFailure, Membership, Venue

TOKEN_SALT = "catalog.door-token"
TOKEN_MAX_AGE = 12 * 60 * 60
MAX_BODY_BYTES = 8192
LOGIN_MAX_FAILURES = 10
LOGIN_MAX_FAILURES_PER_IP = 30
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
LOGIN_FAILURE_RETENTION = timedelta(hours=24)


def error(code, status):
    return JsonResponse({"error": code}, status=status)


def json_body(request):
    """Devuelve (datos, respuesta_de_error); exactamente uno de los dos es None."""
    if request.content_type != "application/json":
        return None, error("expected_json", 415)
    try:
        body = request.body
        if len(body) > MAX_BODY_BYTES:
            raise RequestDataTooBig
        data = json.loads(body)
    except RequestDataTooBig:
        return None, error("request_too_large", 413)
    except (ValueError, RecursionError):
        return None, error("invalid_request", 400)
    if not isinstance(data, dict):
        return None, error("invalid_request", 400)
    return data, None


def password_stamp(user):
    """Huella corta de la contraseña: cambiarla invalida los tokens firmados antes."""
    return user.get_session_auth_hash()[:32]


def issue_token(user):
    return signing.dumps({"uid": user.pk, "h": password_stamp(user)}, salt=TOKEN_SALT)


def user_from_request(request):
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme != "Bearer" or not token:
        return None
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        uid, stamp = payload["uid"], payload["h"]
        user = get_user_model().objects.filter(pk=uid, is_active=True).first()
        if user is None or not isinstance(stamp, str):
            return None
        return user if hmac.compare_digest(stamp.encode(), password_stamp(user).encode()) else None
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None


def bearer_key(request):
    scheme, _, key = request.headers.get("Authorization", "").partition(" ")
    return key if scheme == "Bearer" and key else None


def api_token_user(request):
    """Usuario de una clave de larga duración (`ApiToken`, creada con `create_api_token`) activa."""
    key = bearer_key(request)
    if key is None:
        return None
    token = (
        ApiToken.objects.filter(key_hash=hashlib.sha256(key.encode()).hexdigest(), is_active=True)
        .select_related("user")
        .first()
    )
    return token.user if token and token.user.is_active else None


def bearer_user(request):
    """Usuario del `Authorization: Bearer`, con cualquiera de las dos formas de token.

    Prueba primero la clave de larga duración (`ApiToken`) y después el token firmado de
    12 horas de `POST /api/v1/auth/login/`. Es el único lugar donde se resuelve eso.
    """
    return api_token_user(request) or user_from_request(request)


def _wait(created_ats, limit, now):
    """Segundos de espera si hay `limit` fallos recientes (ordenados del más nuevo al más viejo)."""
    if len(created_ats) < limit:
        return 0
    wait = created_ats[limit - 1] + LOGIN_FAILURE_WINDOW - now
    return max(1, int(wait.total_seconds()) + 1)


def _recent(filters, limit, now):
    return list(
        DoorLoginFailure.objects.filter(created_at__gt=now - LOGIN_FAILURE_WINDOW, **filters)
        .order_by("-created_at").values_list("created_at", flat=True)[:limit]
    )


def login_retry_after(key, ip="", scope=DoorLoginFailure.Scope.DOOR, max_user=LOGIN_MAX_FAILURES,
                      max_ip=LOGIN_MAX_FAILURES_PER_IP):
    """Segundos de espera si se agotaron los intentos; 0 si puede intentar.

    Sin IP confiable, el límite es por usuario (`max_user` fallos en 15 minutos). Con IP, es por
    (usuario, IP) con el mismo umbral, más un tope por IP de `max_ip` fallos con cualquier usuario:
    los fallos de otras IPs no bloquean al usuario. Cada `scope` (portería, admin) cuenta aparte.
    """
    now = timezone.now()
    if not ip:
        return _wait(_recent({"username": key, "scope": scope}, max_user, now), max_user, now)
    return max(
        _wait(_recent({"username": key, "ip": ip, "scope": scope}, max_user, now), max_user, now),
        _wait(_recent({"ip": ip, "scope": scope}, max_ip, now), max_ip, now),
    )


def record_login_failure(key, ip="", scope=DoorLoginFailure.Scope.DOOR):
    DoorLoginFailure.objects.create(username=key, ip=ip or None, scope=scope)
    DoorLoginFailure.objects.filter(created_at__lt=timezone.now() - LOGIN_FAILURE_RETENTION).delete()


def clear_login_failures(key, ip="", scope=DoorLoginFailure.Scope.DOOR):
    """Un login correcto limpia los fallos del usuario (en esa IP, si hay IP confiable)."""
    cleared = DoorLoginFailure.objects.filter(username=key, scope=scope)
    (cleared.filter(ip=ip) if ip else cleared).delete()


def login_user(request):
    """Parte común de los logins: devuelve (usuario, respuesta_de_error).

    Valida el cuerpo, aplica el límite de intentos y las credenciales. Si el
    login es correcto borra los fallos del usuario (en esa IP, si hay IP confiable).
    """
    data, failure = json_body(request)
    if failure:
        return None, failure
    username, password = data.get("username"), data.get("password")
    ip = buyer_ip(request)
    user = None
    key = None
    if isinstance(username, str) and isinstance(password, str):
        key = username.strip().lower()[:150]
        retry_after = login_retry_after(key, ip)
        if retry_after:
            response = JsonResponse({"error": "too_many_attempts", "retryAfter": retry_after}, status=429)
            response["Retry-After"] = str(retry_after)
            return None, response
        user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        if key is not None:
            record_login_failure(key, ip)
        return None, error("invalid_credentials", 401)
    clear_login_failures(key, ip)
    return user, None


def venue_staff_view(*roles):
    """Autentica con el token y exige uno de los roles en el boliche de la URL.

    La vista recibe (request, venue, user, membership, *args, **kwargs).
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, venue_slug, *args, **kwargs):
            user = user_from_request(request)
            if user is None:
                return error("unauthorized", 401)
            membership = (
                Membership.objects.select_related("venue")
                .filter(user=user, venue__slug=venue_slug, venue__is_active=True, role__in=roles)
                .first()
            )
            if membership is None:
                return error("not_found", 404)
            return view(request, membership.venue, user, membership, *args, **kwargs)

        return wrapper

    return decorator


def memberships_data(user):
    memberships = (
        Membership.objects.filter(user=user, venue__is_active=True)
        .select_related("venue").order_by("venue__slug")
    )
    return [
        {"venue": {"slug": m.venue.slug, "name": m.venue.name}, "role": m.role}
        for m in memberships
    ]


@csrf_exempt
@require_POST
@never_cache
def login(request):
    user, failure = login_user(request)
    if failure:
        return failure
    memberships = memberships_data(user)
    if not memberships:
        return error("no_membership", 403)
    return JsonResponse({
        "token": issue_token(user),
        "expiresIn": TOKEN_MAX_AGE,
        "user": {"username": user.get_username()},
        "memberships": memberships,
    })


@require_safe
@never_cache
def me(request):
    user = user_from_request(request)
    if user is None:
        return error("unauthorized", 401)
    return JsonResponse({"user": {"username": user.get_username()}, "memberships": memberships_data(user)})
