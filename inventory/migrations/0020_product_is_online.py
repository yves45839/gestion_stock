from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0019_product_datasheets"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="is_online",
            field=models.BooleanField(default=True, verbose_name="Visible en ligne"),
        ),
    ]
