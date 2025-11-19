from django.db import migrations


def create_default_movement_types(apps, schema_editor):
    MovementType = apps.get_model('inventory', 'MovementType')
    defaults = [
        ("Réception", "RECEPTION", "IN"),
        ("Vente", "VENTE", "OUT"),
        ("Retour client", "RETOUR_CLIENT", "IN"),
        ("Retour fournisseur", "RETOUR_FOURNISSEUR", "OUT"),
        ("Ajustement positif", "AJUSTEMENT_PLUS", "IN"),
        ("Ajustement négatif", "AJUSTEMENT_MOINS", "OUT"),
    ]
    for name, code, direction in defaults:
        MovementType.objects.get_or_create(
            code=code,
            defaults={'name': name, 'direction': direction},
        )


def reverse_movement_types(apps, schema_editor):
    MovementType = apps.get_model('inventory', 'MovementType')
    MovementType.objects.filter(
        code__in=[
            "RECEPTION",
            "VENTE",
            "RETOUR_CLIENT",
            "RETOUR_FOURNISSEUR",
            "AJUSTEMENT_PLUS",
            "AJUSTEMENT_MOINS",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_movement_types, reverse_movement_types),
    ]
