from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from catalog.models import Membership, OrderItem, TicketType, Venue, VenueIntegration

from .forms import FeeForm, MemberForm, VenueCreateForm, VenueForm, sync_staff


def platform_required(view):
    """El panel es solo para el equipo de la plataforma (superusuarios)."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            return render(request, "backoffice/forbidden.html", status=403)
        return view(request, *args, **kwargs)

    return wrapper


class LoginView(auth_views.LoginView):
    template_name = "backoffice/login.html"
    redirect_authenticated_user = True


class SetPasswordView(auth_views.PasswordResetConfirmView):
    template_name = "backoffice/set_password.html"
    success_url = reverse_lazy("backoffice:set-password-done")


def set_password_done(request):
    return render(request, "backoffice/set_password_done.html")


def integrations_for(venue):
    """Una conexión por proveedor, también las que todavía no se crearon."""
    saved = {i.provider: i for i in venue.integrations.all()}
    return [saved.get(p) or VenueIntegration(venue=venue, provider=p) for p in VenueIntegration.Provider.values]


def setup_checklist(venue, integrations):
    mercadopago = next(i for i in integrations if i.provider == VenueIntegration.Provider.MERCADOPAGO)
    return [
        ("Datos y marca", bool(venue.address and venue.logo)),
        ("Comisión definida", venue.fee_changes.exists()),
        ("Administrador asignado", venue.memberships.filter(role=Membership.Role.ADMIN).exists()),
        ("Mercado Pago conectado", mercadopago.status == VenueIntegration.Status.CONNECTED),
        ("Una fecha publicada", venue.events.filter(is_published=True).exists()),
    ]


@platform_required
def venue_list(request):
    now = timezone.now()
    venues = Venue.objects.annotate(
        upcoming=Count("events", filter=Q(events__starts_at__gte=now, events__is_published=True), distinct=True),
        team=Count("memberships", distinct=True),
    ).prefetch_related("integrations")
    rows = []
    for venue in venues:
        mp = next((i for i in venue.integrations.all() if i.provider == VenueIntegration.Provider.MERCADOPAGO), None)
        rows.append({"venue": venue, "mercadopago": mp.get_status_display() if mp else "Sin conectar",
                     "mp_ok": bool(mp and mp.status == VenueIntegration.Status.CONNECTED)})
    return render(request, "backoffice/venue_list.html", {"rows": rows})


@platform_required
def venue_create(request):
    form = VenueCreateForm(request.POST or None)
    fees = FeeForm(request.POST or None, initial={"fee_mode": Venue.FeeMode.BUYER, "buyer_fee_percent": 10,
                                                   "platform_fee_percent": 0})
    if request.method == "POST" and form.is_valid() and fees.is_valid():
        with transaction.atomic():
            venue = form.save(commit=False)
            venue.is_active = False
            fees.save(venue, request.user)
        messages.success(request, f"Creamos {venue.name}. Queda pausado hasta que lo actives.")
        return redirect("backoffice:venue-detail", slug=venue.slug)
    return render(request, "backoffice/venue_create.html", {"form": form, "fees": fees})


def venue_context(venue, tab, **extra):
    return {"venue": venue, "tab": tab, **extra}


@platform_required
def venue_detail(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    now = timezone.now()
    integrations = integrations_for(venue)
    events = (
        venue.events.filter(starts_at__gte=now - timedelta(hours=12))
        .annotate(capacity=Sum("ticket_types__capacity"), sold=Sum("ticket_types__sold"))
        .order_by("starts_at")[:6]
    )
    reserved = OrderItem.objects.filter(
        order__event__venue=venue, order__expires_at__gt=now,
    ).aggregate(total=Sum("quantity"))["total"] or 0
    sold = TicketType.objects.filter(event__venue=venue).aggregate(total=Sum("sold"))["total"] or 0
    return render(request, "backoffice/venue_detail.html", venue_context(
        venue, "summary", events=events, reserved=reserved, sold=sold,
        checklist=setup_checklist(venue, integrations),
        admins=venue.memberships.filter(role=Membership.Role.ADMIN).select_related("user"),
    ))


@platform_required
@require_POST
def venue_toggle(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    venue.is_active = not venue.is_active
    venue.save(update_fields=["is_active"])
    if venue.is_active:
        messages.success(request, f"{venue.name} está activo: sus fechas publicadas se ven en TicketJuy.")
    else:
        messages.success(request, f"{venue.name} quedó pausado: ya no aparece en TicketJuy ni acepta reservas.")
    return redirect("backoffice:venue-detail", slug=venue.slug)


@platform_required
def venue_edit(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    form = VenueForm(request.POST or None, instance=venue)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Guardamos los datos.")
        return redirect("backoffice:venue-edit", slug=venue.slug)
    return render(request, "backoffice/venue_edit.html", venue_context(venue, "edit", form=form))


@platform_required
def venue_fees(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    form = FeeForm(request.POST or None, venue=venue)
    if request.method == "POST" and form.is_valid():
        if form.save(venue, request.user):
            messages.success(request, "Actualizamos la comisión. Los pedidos ya reservados conservan la anterior.")
        else:
            messages.info(request, "La comisión no cambió.")
        return redirect("backoffice:venue-fees", slug=venue.slug)
    history = venue.fee_changes.select_related("changed_by")[:20]
    return render(request, "backoffice/venue_fees.html", venue_context(venue, "fees", form=form, history=history))


def access_link(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return request.build_absolute_uri(reverse("backoffice:set-password", args=[uid, token]))


@platform_required
def venue_team(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    form = MemberForm(request.POST or None)
    link = None
    if request.method == "POST" and form.is_valid():
        membership, created = form.save(venue)
        user = membership.user
        messages.success(request, f"{user.email} tiene el rol {membership.get_role_display()} en {venue.name}.")
        if not user.has_usable_password():
            link = {"email": user.email, "url": access_link(request, user)}
        form = MemberForm()
    members = venue.memberships.select_related("user").order_by("role", "user__email")
    return render(request, "backoffice/venue_team.html", venue_context(
        venue, "team", form=form, members=members, link=link,
    ))


@platform_required
@require_POST
def member_link(request, slug, pk):
    membership = get_object_or_404(Membership, pk=pk, venue__slug=slug)
    venue = membership.venue
    members = venue.memberships.select_related("user").order_by("role", "user__email")
    link = {"email": membership.user.email, "url": access_link(request, membership.user)}
    return render(request, "backoffice/venue_team.html", venue_context(
        venue, "team", form=MemberForm(), members=members, link=link,
    ))


@platform_required
@require_POST
def member_remove(request, slug, pk):
    membership = get_object_or_404(Membership, pk=pk, venue__slug=slug)
    user = membership.user
    membership.delete()
    sync_staff(user)
    messages.success(request, f"{user.email} ya no tiene acceso a {membership.venue.name}.")
    return redirect("backoffice:venue-team", slug=slug)


@platform_required
def venue_integrations(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    return render(request, "backoffice/venue_integrations.html", venue_context(
        venue, "integrations", integrations=integrations_for(venue),
    ))
