import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Esquema: anulación de entradas, ámbito de los fallos de login y payment_ref (todavía nulo)."""

    dependencies = [
        ("catalog", "0019_seguridad"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="voided_at",
            field=models.DateTimeField(blank=True, help_text="Anulada: ya no sirve para entrar.", null=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="voided_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="ticket",
            name="void_reason",
            field=models.CharField(
                blank=True, max_length=10,
                choices=[("refund", "Devolución"), ("chargeback", "Contracargo"), ("manual", "Anulada a mano")],
            ),
        ),
        migrations.AddField(
            model_name="doorloginfailure",
            name="scope",
            field=models.CharField(
                default="door", max_length=10,
                choices=[("door", "Portería y API"), ("admin", "Admin y plataforma")],
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="payment_ref",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
    ]
