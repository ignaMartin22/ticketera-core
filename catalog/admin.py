from django.contrib import admin

from .models import Event, Membership, TicketType, Venue


admin.site.register(Venue)
admin.site.register(Membership)


def managed_venues(user):
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


@admin.register(TicketType)
class TicketTypeAdmin(VenueCatalogAdmin):
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
