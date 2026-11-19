from django.conf import settings
from django.db import models

from catalog.models import Venue


class VenueStatusChange(models.Model):
    """Historial de activar y pausar boliches. El registro más reciente refleja el estado actual."""

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="status_changes")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField()

    class Meta:
        ordering = ["-changed_at", "-pk"]
