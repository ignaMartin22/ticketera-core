from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0012_payment'),
    ]

    operations = [
        migrations.RenameField(
            model_name='order',
            old_name='mp_preference_id',
            new_name='mp_order_id',
        ),
        migrations.AlterField(
            model_name='order',
            name='mp_order_id',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.RemoveField(
            model_name='payment',
            name='mp_payment_id',
        ),
        migrations.AddField(
            model_name='payment',
            name='mp_order_id',
            field=models.CharField(default='', max_length=64, unique=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='payment',
            name='mp_reference',
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
