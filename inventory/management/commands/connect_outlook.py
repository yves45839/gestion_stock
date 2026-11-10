from django.core.management.base import BaseCommand, CommandError
from inventory.delivery.outlook_auth import OutlookAuthError, connect


class Command(BaseCommand):
    help = "Connecter sa boîte Outlook personnelle aux essais locaux, sans envoyer d’email."

    def handle(self, *args, **options):
        try:
            connect(self.stdout.write)
        except OutlookAuthError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(self.style.SUCCESS("Outlook connecté. Aucun email envoyé. Préparez un nouvel essai dans l’application."))
