from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0008_customer_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="amount_paid",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=12,
                verbose_name="Montant payé",
            ),
        ),
    ]
