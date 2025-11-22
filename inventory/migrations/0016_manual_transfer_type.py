from django.db import migrations


def ensure_manual_types(apps, schema_editor):
    MovementType = apps.get_model("inventory", "MovementType")
    defaults = [
        ("Réception", "RECEPTION", "IN"),
        ("Transfert", "TRANSFERT", "OUT"),
    ]
    for name, code, direction in defaults:
        MovementType.objects.update_or_create(
            code=code,
            defaults={"name": name, "direction": direction},
        )


def remove_transfer(apps, schema_editor):
    MovementType = apps.get_model("inventory", "MovementType")
    MovementType.objects.filter(code__in=["TRANSFERT"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0015_seed_initial_users"),
    ]

    operations = [
        migrations.RunPython(ensure_manual_types, remove_transfer),
    ]
