from django.db import migrations


def revoke_venue_staff(apps, schema_editor):
    """El core es solo de la plataforma: la gente de los boliches no entra al /admin/."""
    User = apps.get_model("auth", "User")
    User.objects.filter(is_superuser=False, membership__isnull=False).update(is_staff=False)


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("catalog", "0004_plataforma_comisiones_conexiones"),
    ]

    operations = [migrations.RunPython(revoke_venue_staff, migrations.RunPython.noop)]
