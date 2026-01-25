from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0018_productassetjob_productassetjoblog"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="datasheet_fetched_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Fiche technique r\u00e9cup\u00e9r\u00e9e"),
        ),
        migrations.AddField(
            model_name="product",
            name="datasheet_pdf",
            field=models.FileField(blank=True, null=True, upload_to="products/datasheets", verbose_name="Fiche technique (PDF)"),
        ),
        migrations.AddField(
            model_name="product",
            name="datasheet_url",
            field=models.URLField(blank=True, null=True, verbose_name="Fiche technique (URL)"),
        ),
    ]
