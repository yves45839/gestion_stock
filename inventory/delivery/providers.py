"""Explicit test adapters. No provider is contacted during simulation."""
import re
import base64
import json
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .documents import batch_folder
from .forms import normalize_phone


class DeliveryError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def preflight(batch):
    if batch.simulated != settings.DOCUMENT_DELIVERY_SIMULATE:
        raise DeliveryError("Le mode d’envoi a changé. Préparez à nouveau le document.")
    if not batch.attachments or not all((batch_folder(batch) / item["name"]).is_file() for item in batch.attachments):
        raise DeliveryError("Une pièce jointe a expiré. Préparez à nouveau le document.")
    if "email" in batch.channels:
        sender = settings.DOCUMENT_EMAIL_SENDER
        try:
            validate_email(sender)
        except ValidationError:
            raise DeliveryError("L’adresse email de l’expéditeur configuré est invalide.")
        if batch.destinations.get("sender", "info@label-ci.com").casefold() != sender.casefold():
            raise DeliveryError("L’adresse d’expéditeur a changé. Préparez un nouvel envoi.")
    if batch.simulated:
        return
    if "email" in batch.channels:
        allowed = {address.lower() for address in settings.DOCUMENT_DELIVERY_EMAIL_ALLOWLIST}
        if batch.destinations["email"].lower() not in allowed:
            raise DeliveryError("Cet email ne figure pas parmi les destinataires de test autorisés.")
        if settings.DOCUMENT_MS_AUTH_MODE == "personal":
            from .outlook_auth import connection_ready
            if not getattr(settings, "IS_SALES_DEMO", False) or not connection_ready():
                raise DeliveryError("La boîte Outlook personnelle n’est pas connectée. Lancez connect_outlook, puis préparez un nouvel essai.")
        elif settings.DOCUMENT_MS_AUTH_MODE == "application":
            domain = sender.rsplit("@", 1)[-1].lower()
            if domain in {"outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr", "live.com", "msn.com"}:
                raise DeliveryError("Cette boîte Outlook personnelle nécessite LRSTOCK_MS_AUTH_MODE=personal et la connexion connect_outlook.")
            identifier = r"[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}"
            if not re.fullmatch(identifier, settings.DOCUMENT_MS_TENANT_ID) or not re.fullmatch(identifier, settings.DOCUMENT_MS_CLIENT_ID) or not settings.DOCUMENT_MS_CLIENT_SECRET:
                raise DeliveryError("La connexion Microsoft 365 de l’expéditeur n’est pas configurée.")
        else:
            raise DeliveryError("Choisissez personal ou application pour LRSTOCK_MS_AUTH_MODE.")
        graph_payload(batch)  # Check attachment size before contacting either provider.
    if "whatsapp" in batch.channels:
        try:
            allowed = {normalize_phone(value) for value in settings.DOCUMENT_DELIVERY_PHONE_ALLOWLIST}
        except Exception:
            raise DeliveryError("La liste des numéros de test contient un numéro invalide.")
        if batch.destinations["phone"] not in allowed:
            raise DeliveryError("Ce numéro ne figure pas parmi les destinataires WhatsApp de test autorisés.")
        if not settings.DOCUMENT_WA_TOKEN or not re.fullmatch(r"\d+", settings.DOCUMENT_WA_PHONE_ID) or not re.fullmatch(r"v\d+\.\d+", settings.DOCUMENT_WA_VERSION):
            raise DeliveryError("La connexion WhatsApp Business n’est pas configurée.")
        template = settings.DOCUMENT_WA_IMAGE_TEMPLATE if batch.file_format == "png" else settings.DOCUMENT_WA_PDF_TEMPLATE
        if not re.fullmatch(r"[a-z0-9_]+", template):
            raise DeliveryError("Configurez le modèle WhatsApp approuvé pour ce format.")


def graph_payload(batch):
    attachments = [{"@odata.type": "#microsoft.graph.fileAttachment", "name": item["name"],
        "contentType": item["mimetype"],
        "contentBytes": base64.b64encode((batch_folder(batch) / item["name"]).read_bytes()).decode("ascii")}
        for item in batch.attachments]
    payload = {"message": {"subject": batch.subject,
        "body": {"contentType": "Text", "content": batch.message},
        "from": {"emailAddress": {"name": "LrStock", "address": batch.destinations.get("sender", "info@label-ci.com")}},
        "toRecipients": [{"emailAddress": {"address": batch.destinations["email"]}}],
        "attachments": attachments}, "saveToSentItems": True}
    if len(json.dumps(payload).encode()) > 3 * 1024 * 1024:
        raise DeliveryError("Les pièces jointes dépassent la limite de cet essai Microsoft. Réduisez la période ou choisissez le PDF.")
    return payload


def email_send(batch):
    payload = graph_payload(batch)
    if settings.DOCUMENT_MS_AUTH_MODE == "personal":
        from .outlook_auth import OutlookAuthError, access_token
        try:
            token = access_token()
        except OutlookAuthError as error:
            raise DeliveryError(str(error)) from None
    else:
        try:
            response = requests.post(
                f"https://login.microsoftonline.com/{settings.DOCUMENT_MS_TENANT_ID}/oauth2/v2.0/token",
                data={"client_id": settings.DOCUMENT_MS_CLIENT_ID, "client_secret": settings.DOCUMENT_MS_CLIENT_SECRET,
                      "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
                timeout=20, allow_redirects=False,
            )
            if response.status_code != 200:
                raise DeliveryError(f"Microsoft a refusé l’authentification (HTTP {response.status_code}).")
            token = response.json()["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError("Missing token")
        except (requests.RequestException, ValueError, KeyError):
            raise DeliveryError("La connexion Microsoft a échoué ; aucun email n’a été envoyé.")
    try:
        mailbox = quote(batch.destinations.get("sender", "info@label-ci.com"), safe="@.")
        endpoint = "https://graph.microsoft.com/v1.0/me/sendMail" if settings.DOCUMENT_MS_AUTH_MODE == "personal" else f"https://graph.microsoft.com/v1.0/users/{mailbox}/sendMail"
        response = requests.post(endpoint,
            headers={"Authorization": "Bearer " + token, "client-request-id": str(batch.pk)},
            json=payload, timeout=30, allow_redirects=False)
        if response.status_code != 202:
            raise DeliveryError(f"Microsoft n’a pas confirmé l’envoi (HTTP {response.status_code}).",
                                uncertain=response.status_code >= 500)
        return response.headers.get("request-id") or str(batch.pk)
    except requests.RequestException:
        raise DeliveryError("La confirmation Microsoft n’a pas été reçue. Vérifiez les éléments envoyés avant de renvoyer.", uncertain=True)


def whatsapp_send(batch, attachment):
    base = f"https://graph.facebook.com/{settings.DOCUMENT_WA_VERSION}/{settings.DOCUMENT_WA_PHONE_ID}"
    headers = {"Authorization": "Bearer " + settings.DOCUMENT_WA_TOKEN}
    try:
        with (batch_folder(batch) / attachment["name"]).open("rb") as stream:
            response = requests.post(base + "/media", headers=headers,
                data={"messaging_product": "whatsapp", "type": attachment["mimetype"]},
                files={"file": (attachment["name"], stream, attachment["mimetype"])}, timeout=30, allow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise DeliveryError(f"WhatsApp a refusé la pièce jointe (HTTP {response.status_code}).")
        media_id = response.json()["id"]
    except (requests.RequestException, ValueError, KeyError):
        raise DeliveryError("Le chargement de la pièce jointe WhatsApp a échoué ; aucun message n’a été envoyé.")
    media_type = "image" if batch.file_format == "png" else "document"
    media = {"id": media_id}
    if media_type == "document":
        media["filename"] = attachment["name"]
    template = settings.DOCUMENT_WA_IMAGE_TEMPLATE if media_type == "image" else settings.DOCUMENT_WA_PDF_TEMPLATE
    payload = {"messaging_product": "whatsapp", "to": batch.destinations["phone"].lstrip("+"),
        "type": "template", "template": {"name": template, "language": {"code": settings.DOCUMENT_WA_LANGUAGE},
        "components": [
            {"type": "header", "parameters": [{"type": media_type, media_type: media}]},
            {"type": "body", "parameters": [{"type": "text", "text": " ".join(batch.message.split())}]},
        ]}}
    try:
        response = requests.post(base + "/messages", headers=headers, json=payload, timeout=30, allow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise DeliveryError(f"WhatsApp a refusé le message (HTTP {response.status_code}).")
        return response.json()["messages"][0]["id"]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        raise DeliveryError("La confirmation WhatsApp n’a pas été reçue. Vérifiez avant de renvoyer.", uncertain=True)
