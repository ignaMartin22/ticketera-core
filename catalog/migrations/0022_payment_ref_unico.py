from django.db import migrations, models

import catalog.models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0021_payment_ref_datos")]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="payment_ref",
            field=models.CharField(
                default=catalog.models.new_payment_ref, editable=False, max_length=64, unique=True,
                help_text="Referencia que se manda a las pasarelas. Distinta del id: el id es la llave para ver las entradas.",
            ),
        ),
    ]
