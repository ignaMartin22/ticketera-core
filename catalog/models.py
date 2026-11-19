from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q, Sum


class Venue(models.Model):
    class FeeMode(models.TextChoices):
        VENUE = "venue", "Boliche"
        BUYER = "buyer", "Comprador"

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=150)
    city = models.CharField(max_length=150)
    address = models.CharField(max_length=250)
    instagram = models.CharField(max_length=100, blank=True)
    tagline = models.CharField(max_length=150, blank=True)
    logo = models.CharField(max_length=500, blank=True)
    cover = models.CharField(max_length=500, blank=True)
    flyer_backdrop = models.CharField(max_length=500, blank=True)
    maps_url = models.URLField(blank=True)
    accent = models.CharField(
        max_length=7,
        default="#4fc8ff",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$")],
    )
    fee_mode = models.CharField(
        max_length=5, choices=FeeMode, default=FeeMode.VENUE,
        help_text="Quién paga el cargo de servicio. Con «Boliche», el comprador paga solo el precio.",
    )
    buyer_fee_rate = models.DecimalField(
        max_digits=5, decimal_places=4, default=0,
        help_text="Cargo de servicio que se suma al precio cuando lo paga el comprador (0.1 = 10 %).",
    )
    platform_fee_rate = models.DecimalField(
        max_digits=5, decimal_places=4, default=0,
        help_text="Comisión que retiene la plataforma sobre el precio de cada entrada (0.05 = 5 %).",
    )
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["slug"]
        constraints = [
            models.CheckConstraint(
                condition=Q(buyer_fee_rate__gte=0, buyer_fee_rate__lte=1),
                name="venue_buyer_fee_rate_range",
            ),
            models.CheckConstraint(
                condition=Q(platform_fee_rate__gte=0, platform_fee_rate__lte=1),
                name="venue_platform_fee_rate_range",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def service_fee_rate(self):
        """Cargo que se le suma al comprador."""
        return self.buyer_fee_rate if self.fee_mode == self.FeeMode.BUYER else 0


class FeeChange(models.Model):
    """Historial de comisiones. Cada pedido guarda además los importes con los que se reservó."""

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="fee_changes")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(auto_now_add=True)
    fee_mode = models.CharField(max_length=5, choices=Venue.FeeMode)
    buyer_fee_rate = models.DecimalField(max_digits=5, decimal_places=4)
    platform_fee_rate = models.DecimalField(max_digits=5, decimal_places=4)
    note = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-changed_at", "-pk"]


class VenueIntegration(models.Model):
    """Conexión de un boliche con un servicio externo.

    Los tokens de OAuth se agregan junto con cada integración, cifrados y fuera de este modelo de estado.
    """

    class Provider(models.TextChoices):
        MERCADOPAGO = "mercadopago", "Mercado Pago"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"

    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Sin conectar"
        CONNECTED = "connected", "Conectada"
        EXPIRED = "expired", "Vencida"
        ERROR = "error", "Con error"

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="integrations")
    provider = models.CharField(max_length=20, choices=Provider)
    status = models.CharField(max_length=12, choices=Status, default=Status.DISCONNECTED)
    account_id = models.CharField(max_length=100, blank=True, help_text="Identificador de la cuenta en el proveedor.")
    account_label = models.CharField(max_length=150, blank=True, help_text="Nombre o email de la cuenta vinculada.")
    connected_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["venue", "provider"]
        constraints = [
            models.UniqueConstraint(fields=["venue", "provider"], name="unique_venue_integration"),
        ]

    def __str__(self):
        return f"{self.venue} / {self.get_provider_display()}"


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administración"
        DOOR = "door", "Puerta"
        CASHIER = "cashier", "Caja"

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=7, choices=Role)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["venue", "user"], name="unique_venue_member"),
        ]


class Event(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="events")
    slug = models.SlugField()
    title = models.CharField(max_length=200)
    lineup = models.TextField(blank=True, help_text="Un artista por línea.")
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    doors_note = models.CharField(max_length=200, blank=True)
    age_limit = models.PositiveSmallIntegerField(default=18, validators=[MaxValueValidator(99)])
    dress_code = models.CharField(max_length=120, blank=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ["starts_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["venue", "slug"], name="unique_event_slug_per_venue"),
        ]

    def __str__(self):
        return f"{self.venue} / {self.title}"


class TicketTypeQuerySet(models.QuerySet):
    def with_reserved(self, at):
        return self.annotate(
            reserved=Sum(
                "order_items__quantity",
                filter=Q(order_items__order__expires_at__gt=at),
                default=0,
            ),
        )


class TicketType(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="ticket_types")
    slug = models.SlugField()
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    capacity = models.PositiveIntegerField()
    sold = models.PositiveIntegerField(default=0, editable=False)
    is_active = models.BooleanField(default=True)
    objects = TicketTypeQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "slug"], name="unique_ticket_slug_per_event"),
            models.CheckConstraint(condition=Q(price__gte=0), name="ticket_price_nonnegative"),
            models.CheckConstraint(condition=Q(sold__lte=F("capacity")), name="ticket_sold_within_capacity"),
        ]

    def __str__(self):
        return f"{self.event} / {self.name}"


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="orders")
    buyer_email = models.EmailField()
    buyer_name = models.CharField(max_length=150, blank=True)
    buyer_phone = models.CharField(max_length=16, blank=True, help_text="Formato E.164, por ejemplo +5493884123456.")
    idempotency_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "idempotency_key"], name="unique_order_request_per_event",
            ),
        ]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name="order_items")
    ticket_slug = models.SlugField()
    quantity = models.PositiveSmallIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_service_fee = models.DecimalField(max_digits=13, decimal_places=2)
    unit_platform_fee = models.DecimalField(
        max_digits=13, decimal_places=2, default=0,
        help_text="Comisión de la plataforma sobre el precio, vigente al reservar.",
    )

    class Meta:
        ordering = ["ticket_type_id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "ticket_type"], name="unique_order_ticket_type"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="order_item_quantity_positive"),
        ]
