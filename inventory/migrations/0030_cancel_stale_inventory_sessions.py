"""Annule les sessions d'inventaire ouvertes depuis plus de 7 jours.

Avant la refonte de l'inventaire physique, une session se créait
implicitement à la visite de la page et personne ne la clôturait : des
sessions « ouvertes » traînent en base avec des quantités comptées vieilles
de plusieurs jours ou mois. Les clôturer générerait des ajustements de
stock faux ; elles sont donc annulées (aucun ajustement), et l'équipe
repartira d'une session fraîche.
"""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone


STALE_AFTER_DAYS = 7


def cancel_stale_sessions(apps, schema_editor):
    InventoryCountSession = apps.get_model("inventory", "InventoryCountSession")
    now = timezone.now()
    cutoff = now - timedelta(days=STALE_AFTER_DAYS)
    stale = InventoryCountSession.objects.filter(status="open", started_at__lt=cutoff)
    for session in stale:
        session.status = "cancelled"
        session.closed_at = now
        note = (
            "Annulée automatiquement (migration 0030) : session ouverte depuis"
            f" plus de {STALE_AFTER_DAYS} jours, comptages obsolètes."
        )
        session.notes = f"{session.notes}\n{note}".strip()
        session.save(update_fields=["status", "closed_at", "notes", "updated_at"])


def noop(apps, schema_editor):
    """Irréversible sans perte d'information : les sessions restent en base."""


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0029_alter_inventorycountsession_status"),
    ]

    operations = [
        migrations.RunPython(cancel_stale_sessions, noop),
    ]
