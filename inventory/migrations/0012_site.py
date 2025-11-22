from django.conf import settings
from django.db import migrations, models


def _create_initial_sites(apps, schema_editor):
    Site = apps.get_model("inventory", "Site")
    StockMovement = apps.get_model("inventory", "StockMovement")
    names = ["Treichville", "Riviera 2", "Abobo"]
    created_sites = []
    for name in names:
        site, _ = Site.objects.get_or_create(name=name, defaults={"description": ""})
        created_sites.append(site)
    default_site = created_sites[0] if created_sites else None
    if default_site:
        StockMovement.objects.filter(site__isnull=True).update(site=default_site)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0011_product_image"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Site",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=150, unique=True)),
                ("description", models.TextField(blank=True)),
            ],
            options={
                "ordering": ["name"],
                "verbose_name": "site",
                "verbose_name_plural": "sites",
            },
        ),
        migrations.CreateModel(
            name="SiteAssignment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="assignments",
                        to="inventory.site",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=models.CASCADE,
                        related_name="site_assignment",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "site assigné",
                "verbose_name_plural": "sites assignés",
            },
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="site",
            field=models.ForeignKey(
                null=True,
                on_delete=models.PROTECT,
                related_name="stock_movements",
                to="inventory.site",
            ),
        ),
        migrations.RunPython(_create_initial_sites, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="stockmovement",
            name="site",
            field=models.ForeignKey(
                on_delete=models.PROTECT,
                related_name="stock_movements",
                to="inventory.site",
            ),
        ),
    ]
