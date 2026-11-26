from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import transaction

from catalog.models import FeeChange, Membership, Venue

BRAND_FIELDS = ["name", "city", "address", "instagram", "tagline", "maps_url", "accent", "logo", "cover", "flyer_backdrop"]


class VenueForm(forms.ModelForm):
    class Meta:
        model = Venue
        fields = BRAND_FIELDS
        labels = {
            "name": "Nombre",
            "city": "Ciudad",
            "address": "Dirección",
            "instagram": "Instagram",
            "tagline": "Frase",
            "maps_url": "Link de Google Maps",
            "accent": "Color de acento",
            "logo": "Logo",
            "cover": "Foto de portada",
            "flyer_backdrop": "Fondo de los flyers",
        }
        help_texts = {
            "instagram": "Usuario sin @.",
            "tagline": "Aparece debajo del nombre, por ejemplo «Noches infinitas».",
            "logo": "Ruta del archivo en el frontend del boliche, por ejemplo /brand/infinity/infinity-logo.svg.",
            "cover": "Ruta del archivo en el frontend del boliche.",
            "flyer_backdrop": "Ruta del archivo en el frontend del boliche.",
        }
        widgets = {"accent": forms.TextInput(attrs={"type": "color"})}

    def clean_instagram(self):
        return self.cleaned_data["instagram"].strip().lstrip("@")


class VenueCreateForm(VenueForm):
    class Meta(VenueForm.Meta):
        fields = ["slug", *BRAND_FIELDS]
        labels = {**VenueForm.Meta.labels, "slug": "Identificador"}
        help_texts = {
            **VenueForm.Meta.help_texts,
            "slug": "Identifica al boliche en la API (/api/v1/venues/infinity/). Minúsculas, números y guiones. No se puede cambiar después.",
        }

    def clean_slug(self):
        return self.cleaned_data["slug"].lower()


def as_percent(rate):
    """0.1 -> «10», sin notación científica."""
    return format((Decimal(rate) * 100).normalize(), "f")


def percent_field(label, help_text):
    return forms.DecimalField(
        label=label, help_text=help_text, max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
    )


class FeeForm(forms.Form):
    """Las tasas se cargan en porcentaje y se guardan como fracción (10 -> 0.1)."""

    fee_mode = forms.ChoiceField(
        label="Quién paga el cargo de servicio", choices=Venue.FeeMode.choices, widget=forms.RadioSelect,
    )
    buyer_fee_percent = percent_field(
        "Cargo de servicio al comprador (%)", "Se suma al precio de cada entrada. Solo aplica si lo paga el comprador.",
    )
    platform_fee_percent = percent_field(
        "Comisión de la plataforma (%)", "Se descuenta del precio de cada entrada antes de pagarle al boliche.",
    )
    note = forms.CharField(
        label="Motivo del cambio", required=False, max_length=250,
        help_text="Queda en el historial. Por ejemplo, «acuerdo de lanzamiento».",
    )

    def __init__(self, *args, venue=None, **kwargs):
        if venue is not None and "initial" not in kwargs:
            kwargs["initial"] = {
                "fee_mode": venue.fee_mode,
                "buyer_fee_percent": as_percent(venue.buyer_fee_rate),
                "platform_fee_percent": as_percent(venue.platform_fee_rate),
            }
        super().__init__(*args, **kwargs)

    def rates(self):
        data = self.cleaned_data
        return data["fee_mode"], data["buyer_fee_percent"] / Decimal(100), data["platform_fee_percent"] / Decimal(100)

    @transaction.atomic
    def save(self, venue, user):
        """Aplica la comisión y la registra en el historial si cambió. Devuelve True si cambió."""
        fee_mode, buyer_rate, platform_rate = self.rates()
        current = (venue.fee_mode, venue.buyer_fee_rate, venue.platform_fee_rate)
        if venue.pk and current == (fee_mode, buyer_rate, platform_rate):
            return False
        venue.fee_mode, venue.buyer_fee_rate, venue.platform_fee_rate = fee_mode, buyer_rate, platform_rate
        venue.save()
        FeeChange.objects.create(
            venue=venue, changed_by=user, fee_mode=fee_mode, buyer_fee_rate=buyer_rate,
            platform_fee_rate=platform_rate, note=self.cleaned_data["note"],
        )
        return True


class MemberForm(forms.Form):
    email = forms.EmailField(label="Email")
    name = forms.CharField(label="Nombre", required=False, max_length=150)
    role = forms.ChoiceField(label="Rol", choices=Membership.Role.choices)

    @transaction.atomic
    def save(self, venue):
        """Crea el usuario si no existe y le asigna el rol. Devuelve (membresía, usuario nuevo).

        El usuario no entra al core: va a usar el dashboard del repo de su boliche.
        """
        User = get_user_model()
        email = self.cleaned_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
        created = user is None
        if created:
            user = User(username=email, email=email, first_name=self.cleaned_data["name"])
            user.set_unusable_password()
            user.save()
        membership, _ = Membership.objects.update_or_create(
            venue=venue, user=user, defaults={"role": self.cleaned_data["role"]},
        )
        return membership, created


class GatewayForm(forms.Form):
    """Credenciales de una pasarela sin reparto, tal como las entrega el banco.

    Las claves secretas nunca se vuelven a mostrar: para cambiarlas hay que cargarlas de nuevo.
    """

    FIELDS = {
        "macro": [
            ("comercio", "ID de comercio", False, True),
            ("secret", "Clave secreta (frase)", True, True),
            ("sucursal", "ID de sucursal", False, False),
        ],
        "nave": [
            ("client_id", "Client ID", False, True),
            ("client_secret", "Client secret", True, True),
            ("pos_id", "POS ID", False, True),
        ],
    }

    live_mode = forms.BooleanField(
        label="Credenciales de producción", required=False,
        help_text="Sin marcar, se usa el entorno de prueba del banco: no se cobra de verdad.",
    )

    def __init__(self, *args, provider, **kwargs):
        super().__init__(*args, prefix=provider, **kwargs)
        self.provider = provider
        for name, label, secret, required in self.FIELDS[provider]:
            attrs = {"autocomplete": "off", "spellcheck": "false"}
            widget = forms.PasswordInput(attrs=attrs, render_value=False) if secret else forms.TextInput(attrs=attrs)
            self.fields[name] = forms.CharField(label=label, max_length=200, required=required, strip=True, widget=widget)
        self.order_fields([name for name, *_ in self.FIELDS[provider]] + ["live_mode"])

    def credentials(self):
        return {name: self.cleaned_data[name] for name, *_ in self.FIELDS[self.provider]}

    def account_id(self):
        return self.cleaned_data["comercio" if self.provider == "macro" else "pos_id"]

