from datetime import timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from catalog.models import Membership, Order, OrderItem, TicketType, Venue, VenueIntegration

from .forms import FeeForm, MemberForm, VenueCreateForm, VenueForm
from .models import VenueStatusChange


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
        order__event__venue=venue, order__expires_at__gt=now, order__paid_at__isnull=True,
    ).aggregate(total=Sum("quantity"))["total"] or 0
    sold = TicketType.objects.filter(event__venue=venue).aggregate(total=Sum("sold"))["total"] or 0
    return render(request, "backoffice/venue_detail.html", venue_context(
        venue, "summary", events=events, reserved=reserved, sold=sold,
        checklist=setup_checklist(venue, integrations),
        admins=venue.memberships.filter(role=Membership.Role.ADMIN).select_related("user"),
        status_changes=venue.status_changes.select_related("changed_by")[:5],
    ))


@platform_required
@require_POST
def venue_toggle(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    venue.is_active = not venue.is_active
    with transaction.atomic():
        venue.save(update_fields=["is_active"])
        VenueStatusChange.objects.create(venue=venue, changed_by=request.user, is_active=venue.is_active)
    if venue.is_active:
        messages.success(request, f"{venue.name} está activo: la API expone sus fechas publicadas.")
    else:
        messages.success(request, f"{venue.name} quedó pausado: la API ya no expone sus fechas ni acepta reservas.")
    return redirect("backoffice:venue-detail", slug=venue.slug)


def sales_rows(venue, now):
    """Una fila por fecha: vendidas, reservas vigentes, cupo, ocupación y recaudación a precio de entrada."""
    events = (
        venue.events.annotate(
            capacity=Sum("ticket_types__capacity"),
            sold=Sum("ticket_types__sold"),
            revenue=Sum(ExpressionWrapper(
                F("ticket_types__sold") * F("ticket_types__price"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )),
        )
        .order_by("-starts_at")
        .prefetch_related("ticket_types")
    )
    reserved = dict(
        OrderItem.objects.filter(
            order__event__venue=venue, order__expires_at__gt=now, order__paid_at__isnull=True,
        )
        .values("order__event")
        .annotate(total=Sum("quantity"))
        .values_list("order__event", "total")
    )
    rows = []
    for event in events:
        sold, capacity = event.sold or 0, event.capacity or 0
        rows.append({
            "event": event, "sold": sold, "reserved": reserved.get(event.pk, 0),
            "capacity": capacity, "revenue": event.revenue or Decimal(0),
            "occupancy": round(sold * 100 / capacity) if capacity else 0,
        })
    return rows


@platform_required
def venue_orders(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    now = timezone.now()
    orders = (
        Order.objects.filter(event__venue=venue)
        .select_related("event")
        .annotate(
            ticket_count=Sum("items__quantity"),
            total=Sum(ExpressionWrapper(
                F("items__quantity") * (F("items__unit_price") + F("items__unit_service_fee")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )),
        )
        .order_by("-created_at")[:100]
    )
    return render(request, "backoffice/venue_orders.html", venue_context(
        venue, "orders", orders=orders, sales=sales_rows(venue, now), now=now,
    ))


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


@platform_required
def venue_team(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    form = MemberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        membership, _ = form.save(venue)
        messages.success(
            request, f"{membership.user.email} tiene el rol {membership.get_role_display()} en {venue.name}.",
        )
        return redirect("backoffice:venue-team", slug=venue.slug)
    members = venue.memberships.select_related("user").order_by("role", "user__email")
    return render(request, "backoffice/venue_team.html", venue_context(venue, "team", form=form, members=members))


@platform_required
@require_POST
def member_remove(request, slug, pk):
    membership = get_object_or_404(Membership, pk=pk, venue__slug=slug)
    user = membership.user
    membership.delete()
    messages.success(request, f"{user.email} ya no tiene acceso a {membership.venue.name}.")
    return redirect("backoffice:venue-team", slug=slug)


@platform_required
def venue_integrations(request, slug):
    venue = get_object_or_404(Venue, slug=slug)
    return render(request, "backoffice/venue_integrations.html", venue_context(
        venue, "integrations", integrations=integrations_for(venue),
    ))
