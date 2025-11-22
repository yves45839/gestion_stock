from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

import inventory.models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_alter_saleitem_options_saleitem_description_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Customer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "reference",
                    models.CharField(
                        default=inventory.models.generate_customer_reference,
                        max_length=20,
                        unique=True,
                        verbose_name="Référence client",
                    ),
                ),
                ("name", models.CharField(max_length=255, verbose_name="Nom du contact")),
                ("company_name", models.CharField(blank=True, max_length=255, verbose_name="Entreprise")),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=50)),
                ("address", models.TextField(blank=True)),
                (
                    "credit_limit",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=12,
                        verbose_name="Plafond de crédit",
                    ),
                ),
                ("notes", models.TextField(blank=True)),
            ],
            options={
                "verbose_name": "client",
                "verbose_name_plural": "clients",
                "ordering": ["name", "company_name"],
            },
        ),
        migrations.CreateModel(
            name="CustomerAccountEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "entry_type",
                    models.CharField(
                        choices=[("debit", "Débit"), ("credit", "Crédit")],
                        default="debit",
                        max_length=10,
                    ),
                ),
                ("label", models.CharField(max_length=255, verbose_name="Libellé")),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="Date")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("notes", models.TextField(blank=True)),
                (
                    "customer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entries",
                        to="inventory.customer",
                    ),
                ),
            ],
            options={
                "verbose_name": "mouvement de compte client",
                "verbose_name_plural": "mouvements de comptes client",
                "ordering": ["-occurred_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="sale",
            name="customer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sales",
                to="inventory.customer",
            ),
        ),
        migrations.AddField(
            model_name="customeraccountentry",
            name="sale",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="account_entries",
                to="inventory.sale",
            ),
        ),
    ]
