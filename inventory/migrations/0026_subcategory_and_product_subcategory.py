from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0025_alter_product_datasheet_fetched_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=150)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subcategories",
                        to="inventory.category",
                    ),
                ),
            ],
            options={
                "verbose_name": "sous-catégorie",
                "verbose_name_plural": "sous-catégories",
                "ordering": ["category__name", "name"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("category", "name"),
                        name="inventory_subcategory_unique_per_category",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="product",
            name="subcategory",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="inventory.subcategory",
            ),
        ),
    ]
