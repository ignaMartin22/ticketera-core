from django import forms
from django.contrib import admin, messages
from django.utils import timezone

from .models import Event, Membership, Order, Payment, Ticket, TicketType, Venue
from .orders import OrderConflict
from .tickets import issue_tickets


admin.site.register(Venue)
admin.site.register(Membership)


def managed_venues(user):
    # Solo el equipo de la plataforma entra al /admin/. Si alguna vez un usuario de staff
    # tiene membresías, el catálogo igual queda limitado a sus boliches.
    if not user.is_authenticated:
        return Venue.objects.none()
    if user.is_superuser:
        return Venue.objects.all()
    return Venue.objects.filter(memberships__user=user, memberships__role=Membership.Role.ADMIN)


class VenueCatalogAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return managed_venues(request.user).exists()

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return managed_venues(request.user).filter(pk=self.venue_id(obj)).exists()

    has_view_permission = has_change_permission

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(Event)
class EventAdmin(VenueCatalogAdmin):
    list_display = ["title", "venue", "starts_at", "is_published"]
    # Solo ofrece los boliches que el usuario ya ve en la lista.
    list_filter = [("venue", admin.RelatedOnlyFieldListFilter)]
    list_select_related = ["venue"]

    def venue_id(self, obj):
        return obj.venue_id

    def get_queryset(self, request):
        return super().get_queryset(request).filter(venue__in=managed_venues(request.user))

    def get_readonly_fields(self, request, obj=None):
        return ("venue",) if obj else ()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "venue":
            kwargs["queryset"] = managed_venues(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class TicketTypeAdminForm(forms.ModelForm):
    def clean_capacity(self):
        capacity = self.cleaned_data["capacity"]
        if self.instance.pk:
            # Admin processes changes in a transaction. Lock the same row as reserve_order.
            ticket = TicketType.objects.select_for_update().get(pk=self.instance.pk)
            stock = TicketType.objects.with_reserved(timezone.now()).get(pk=ticket.pk)
            if capacity < stock.sold + stock.reserved:
                raise forms.ValidationError("El cupo debe cubrir las entradas vendidas y reservadas.")
        return capacity


@admin.register(TicketType)
class TicketTypeAdmin(VenueCatalogAdmin):
    form = TicketTypeAdminForm
    list_display = ["name", "event", "price", "capacity", "sold", "is_active"]
    list_select_related = ["event", "event__venue"]

    def venue_id(self, obj):
        return obj.event.venue_id

    def get_queryset(self, request):
        return super().get_queryset(request).filter(event__venue__in=managed_venues(request.user))

    def get_readonly_fields(self, request, obj=None):
        return ("event", "sold") if obj else ("sold",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "event":
            kwargs["queryset"] = Event.objects.filter(
                venue__in=managed_venues(request.user),
            ).select_related("venue")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["event", "buyer_name", "buyer_email", "buyer_phone", "status", "created_at"]
    list_select_related = ["event", "event__venue"]
    actions = ["mark_paid_and_issue"]

    def has_module_permission(self, request):
        return managed_venues(request.user).exists()

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return managed_venues(request.user).filter(pk=obj.event.venue_id).exists()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(event__venue__in=managed_venues(request.user))

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.display(description="Estado")
    def status(self, obj):
        if obj.paid_at is not None:
            return "Pagado"
        return "Pendiente" if obj.expires_at > timezone.now() else "Vencido"

    @admin.action(description="Marcar como pagado y emitir entradas", permissions=["view"])
    def mark_paid_and_issue(self, request, queryset):
        issued = 0
        for order in queryset:
            try:
                issue_tickets(order.pk)
            except OrderConflict:
                self.message_user(
                    request, f"Sin cupo para emitir el pedido {order.pk}.", level=messages.ERROR,
                )
            else:
                issued += 1
        if issued:
            self.message_user(request, f"Entradas emitidas en {issued} pedido(s).", level=messages.SUCCESS)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["order", "event", "buyer", "amount", "status", "outcome", "created_at"]
    list_select_related = ["order", "order__event"]
    list_filter = ["outcome"]
    actions = None

    def has_module_permission(self, request):
        return managed_venues(request.user).exists()

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return managed_venues(request.user).filter(pk=obj.order.event.venue_id).exists()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(order__event__venue__in=managed_venues(request.user))

    @admin.display(description="Evento", ordering="order__event")
    def event(self, obj):
        return obj.order.event

    @admin.display(description="Comprador", ordering="order__buyer_name")
    def buyer(self, obj):
        return obj.order.buyer_name


class AttendedFilter(admin.SimpleListFilter):
    title = "ingresó"
    parameter_name = "attended"

    def lookups(self, request, model_admin):
        return [("yes", "Sí"), ("no", "No")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(used_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(used_at__isnull=True)
        return queryset


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ["event", "holder", "ticket_type", "attended", "used_at", "used_by"]
    list_select_related = ["event", "order", "ticket_type", "used_by"]
    list_filter = ["event", AttendedFilter]
    actions = None

    def has_module_permission(self, request):
        return managed_venues(request.user).exists()

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return self.has_module_permission(request)
        return managed_venues(request.user).filter(pk=obj.event.venue_id).exists()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(event__venue__in=managed_venues(request.user))

    @admin.display(description="Titular", ordering="order__buyer_name")
    def holder(self, obj):
        return obj.order.buyer_name

    @admin.display(description="Ingresó", boolean=True, ordering="used_at")
    def attended(self, obj):
        return obj.used_at is not None
