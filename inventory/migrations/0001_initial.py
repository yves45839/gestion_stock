# Generated manually
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Brand',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=150, unique=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=150, unique=True)),
            ],
            options={
                'ordering': ['name'],
                'verbose_name': 'catégorie',
                'verbose_name_plural': 'catégories',
            },
        ),
        migrations.CreateModel(
            name='MovementType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=150)),
                ('code', models.CharField(max_length=50, unique=True)),
                ('direction', models.CharField(choices=[('IN', 'Entrée'), ('OUT', 'Sortie')], default='IN', max_length=3)),
            ],
            options={
                'ordering': ['name'],
                'verbose_name': 'type de mouvement',
                'verbose_name_plural': 'types de mouvements',
            },
        ),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sku', models.CharField(max_length=100, unique=True, verbose_name='SKU')),
                ('manufacturer_reference', models.CharField(blank=True, max_length=100, verbose_name='Référence fabricant')),
                ('name', models.CharField(max_length=255, verbose_name='Nom')),
                ('description', models.TextField(blank=True)),
                ('purchase_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Prix d'achat")),
                ('sale_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='Prix de vente')),
                ('brand', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='products', to='inventory.brand')),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='products', to='inventory.category')),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='StockMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.PositiveIntegerField()),
                ('movement_date', models.DateTimeField(default=django.utils.timezone.now)),
                ('comment', models.TextField(blank=True)),
                ('document_number', models.CharField(blank=True, max_length=100)),
                ('movement_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to='inventory.movementtype')),
                ('performed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_movements', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stock_movements', to='inventory.product')),
            ],
            options={
                'ordering': ['-movement_date', '-id'],
                'verbose_name': 'mouvement de stock',
                'verbose_name_plural': 'mouvements de stock',
            },
        ),
    ]
