from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0020_product_is_online"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="pending_image",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to="products/images/pending",
                verbose_name="Aperçu image IA",
            ),
        ),
    ]
