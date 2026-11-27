import secrets

from django.db import migrations


def fill_payment_ref(apps, schema_editor):
    """Cada pedido existente recibe su propia referencia (un default callable no la daría distinta por fila)."""
    Order = apps.get_model("catalog", "Order")
    for order in Order.objects.filter(payment_ref__isnull=True).only("pk").iterator():
        Order.objects.filter(pk=order.pk).update(payment_ref=secrets.token_hex(16))


class Migration(migrations.Migration):
    dependencies = [("catalog", "0020_anular_pagoref_scope")]

    operations = [migrations.RunPython(fill_payment_ref, migrations.RunPython.noop)]
