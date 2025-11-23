from django.db import migrations
from django.contrib.auth.hashers import make_password


def create_initial_users(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Site = apps.get_model("inventory", "Site")
    SiteAssignment = apps.get_model("inventory", "SiteAssignment")

    default_password = "Stock@2024"

    users = [
        {
            "username": "anderson",
            "first_name": "Anderson",
            "last_name": "Abobo",
            "site_name": "Abobo",
            "site_description": "Stock principal d'Anderson à Abobo",
        },
        {
            "username": "natacha",
            "first_name": "Natacha",
            "last_name": "Riviera 2",
            "site_name": "Riviera 2",
            "site_description": "Stock de Natacha situé à Riviera 2",
        },
        {
            "username": "jean_jacques",
            "first_name": "Jean Jacques",
            "last_name": "Treichville",
            "site_name": "Treichville",
            "site_description": "Stock de Jean Jacques à Treichville",
        },
    ]

    for entry in users:
        site, _ = Site.objects.get_or_create(
            name=entry["site_name"],
            defaults={"description": entry["site_description"]},
        )
        user, created = User.objects.get_or_create(
            username=entry["username"],
            defaults={
                "first_name": entry["first_name"],
                "last_name": entry["last_name"],
            },
        )
        update_fields = []
        if user.first_name != entry["first_name"]:
            user.first_name = entry["first_name"]
            update_fields.append("first_name")
        if user.last_name != entry["last_name"]:
            user.last_name = entry["last_name"]
            update_fields.append("last_name")
        if created or not user.password:
            user.password = make_password(default_password)
            update_fields.append("password")
        if update_fields:
            user.save(update_fields=update_fields)
        SiteAssignment.objects.update_or_create(user=user, defaults={"site": site})


def remove_initial_users(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Site = apps.get_model("inventory", "Site")
    SiteAssignment = apps.get_model("inventory", "SiteAssignment")

    usernames = ["anderson", "natacha", "jean_jacques"]
    site_names = ["Abobo", "Riviera 2", "Treichville"]

    SiteAssignment.objects.filter(user__username__in=usernames).delete()
    User.objects.filter(username__in=usernames).delete()
    Site.objects.filter(name__in=site_names, assignments__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0014_saleitem_returned_quantity"),
    ]

    operations = [
        migrations.RunPython(create_initial_users, reverse_code=remove_initial_users),
    ]
