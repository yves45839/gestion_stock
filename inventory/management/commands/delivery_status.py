"""Safe local diagnostics: no secret values, no network calls and no mail sent."""
from pathlib import Path
import os

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Afficher le mode des essais d’envoi et la présence de leur configuration, sans secrets."

    def handle(self, *args, **options):
        env_file = Path(settings.BASE_DIR) / ".env"
        self.stdout.write(f"Fichier de configuration : {env_file}")
        self.stdout.write("Fichier .env : " + ("présent" if env_file.is_file() else "ABSENT"))
        if env_file.with_name(".env.txt").is_file():
            self.stdout.write(self.style.WARNING("Un fichier .env.txt existe : Windows doit conserver exactement le nom .env."))
        self.stdout.write(f"Paramètres chargés : {os.environ.get('DJANGO_SETTINGS_MODULE', 'inconnus')}")
        if not settings.DOCUMENT_DELIVERY_ENABLED:
            mode = "DÉSACTIVÉ"
        else:
            mode = "SIMULATION — aucun message réel" if settings.DOCUMENT_DELIVERY_SIMULATE else "ESSAI RÉEL — confirmation et destinataires autorisés requis"
        self.stdout.write(self.style.SUCCESS("Mode d’envoi : " + mode))
        self.stdout.write(f"Expéditeur email : {settings.DOCUMENT_EMAIL_SENDER}")
        self.stdout.write(f"Destinataires email de test autorisés : {len(settings.DOCUMENT_DELIVERY_EMAIL_ALLOWLIST)}")
        self.stdout.write(f"Numéros WhatsApp de test autorisés : {len(settings.DOCUMENT_DELIVERY_PHONE_ALLOWLIST)}")
        self.stdout.write(f"Type de connexion Microsoft : {settings.DOCUMENT_MS_AUTH_MODE}")
        if settings.DOCUMENT_MS_AUTH_MODE == "personal":
            from inventory.delivery.outlook_auth import connection_ready
            self.stdout.write("Outlook personnel : " + ("connexion enregistrée (non vérifiée en ligne)" if connection_ready() else "NON CONNECTÉ — lancer connect_outlook"))
            microsoft_ready = bool(settings.DOCUMENT_MS_CLIENT_ID)
        else:
            microsoft_ready = all((settings.DOCUMENT_MS_TENANT_ID, settings.DOCUMENT_MS_CLIENT_ID, settings.DOCUMENT_MS_CLIENT_SECRET))
        self.stdout.write("Paramètres Microsoft : " + ("renseignés (autorisation non vérifiée)" if microsoft_ready else "INCOMPLETS"))
        self.stdout.write("Aucun secret affiché. Aucune connexion Microsoft/Meta et aucun envoi effectués.")
        if settings.DOCUMENT_DELIVERY_ENABLED and settings.DOCUMENT_DELIVERY_SIMULATE:
            self.stdout.write("Pour un essai réel : LRSTOCK_DELIVERY_SIMULATE=false dans .env, puis redémarrer avec py -3.12 start_demo.py --delivery-test.")
