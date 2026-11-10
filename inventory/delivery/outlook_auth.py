"""Delegated Outlook personal-account login for local delivery trials only."""
import json
import os
from pathlib import Path
import tempfile
import time

import requests
from django.conf import settings

AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
SCOPES = "https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access"


class OutlookAuthError(Exception):
    pass


def _cache_path():
    return Path(settings.DOCUMENT_MS_AUTH_CACHE)


def _read_cache():
    try:
        cache = json.loads(_cache_path().read_text(encoding="utf-8"))
        if (cache.get("client_id") != settings.DOCUMENT_MS_CLIENT_ID or
                cache.get("account", "").casefold() != settings.DOCUMENT_EMAIL_SENDER.casefold()):
            return {}
        cache["expires_at"] = float(cache.get("expires_at", 0))
        return cache
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def connection_ready():
    cache = _read_cache()
    return bool(cache.get("refresh_token") or (cache.get("access_token") and cache.get("expires_at", 0) > time.time() + 60))


def _save_cache(cache):
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".outlook-auth-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(cache, output)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _token_cache(data, account, previous_refresh=""):
    if not isinstance(data, dict):
        raise OutlookAuthError("La réponse Microsoft est invalide. Relancez connect_outlook.")
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise OutlookAuthError("Microsoft n’a pas fourni de connexion valide.")
    scopes = {value.rsplit("/", 1)[-1].lower() for value in data.get("scope", "").split()}
    if scopes and "mail.send" not in scopes:
        raise OutlookAuthError("L’autorisation déléguée Mail.Send n’a pas été accordée.")
    return {"client_id": settings.DOCUMENT_MS_CLIENT_ID, "account": account,
            "access_token": token, "refresh_token": data.get("refresh_token") or previous_refresh,
            "expires_at": time.time() + int(data.get("expires_in", 3600))}


def access_token():
    if not getattr(settings, "IS_SALES_DEMO", False):
        raise OutlookAuthError("La connexion Outlook personnelle est réservée à ces essais isolés.")
    cache = _read_cache()
    if not cache:
        raise OutlookAuthError("Connectez Outlook avec la commande connect_outlook avant l’envoi.")
    if cache.get("expires_at", 0) > time.time() + 60 and cache.get("access_token"):
        return cache["access_token"]
    if not cache.get("refresh_token"):
        raise OutlookAuthError("La connexion Outlook a expiré. Relancez connect_outlook.")
    try:
        response = requests.post(AUTHORITY + "/token", data={"client_id": settings.DOCUMENT_MS_CLIENT_ID,
            "grant_type": "refresh_token", "refresh_token": cache["refresh_token"], "scope": SCOPES}, timeout=20, allow_redirects=False)
        if response.status_code != 200:
            raise OutlookAuthError("Microsoft demande une nouvelle connexion. Relancez connect_outlook.")
        updated = _token_cache(response.json(), cache["account"], cache["refresh_token"])
        _save_cache(updated)
        return updated["access_token"]
    except (requests.RequestException, ValueError, KeyError, OSError):
        raise OutlookAuthError("La connexion Outlook n’a pas pu être renouvelée. Réessayez ou relancez connect_outlook.")


def connect(display, pause=time.sleep, monotonic=time.monotonic):
    if not getattr(settings, "IS_SALES_DEMO", False) or settings.DOCUMENT_MS_AUTH_MODE != "personal":
        raise OutlookAuthError("Pour ces essais, choisissez LRSTOCK_MS_AUTH_MODE=personal dans .env.")
    if not settings.DOCUMENT_MS_CLIENT_ID:
        raise OutlookAuthError("Renseignez l’ID d’application client dans LRSTOCK_MS_CLIENT_ID.")
    try:
        response = requests.post(AUTHORITY + "/devicecode", data={"client_id": settings.DOCUMENT_MS_CLIENT_ID,
            "scope": SCOPES}, timeout=20, allow_redirects=False)
        if response.status_code != 200:
            raise OutlookAuthError("Microsoft refuse cette application. Vérifiez qu’elle accepte les comptes personnels et autorise les flux de clients publics.")
        flow = response.json()
        code = flow["device_code"]
        interval = max(5, min(int(flow.get("interval", 5)), 15))
        deadline = monotonic() + min(int(flow.get("expires_in", 900)), 900)
        display("Ouvrez https://microsoft.com/devicelogin dans votre navigateur.")
        display("Code temporaire à saisir uniquement chez Microsoft : " + flow["user_code"])
        display("Connectez-vous avec " + settings.DOCUMENT_EMAIL_SENDER + " et acceptez les autorisations de LrStock. Aucun email ne sera envoyé par cette connexion.")
        while monotonic() < deadline:
            pause(interval)
            response = requests.post(AUTHORITY + "/token", data={"client_id": settings.DOCUMENT_MS_CLIENT_ID,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": code}, timeout=20, allow_redirects=False)
            data = response.json()
            if response.status_code == 200:
                cache = _token_cache(data, settings.DOCUMENT_EMAIL_SENDER)
                profile = requests.get("https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
                    headers={"Authorization": "Bearer " + cache["access_token"]}, timeout=20, allow_redirects=False)
                if profile.status_code != 200:
                    raise OutlookAuthError("Le compte connecté n’a pas pu être vérifié. Vérifiez l’autorisation déléguée User.Read.")
                account = profile.json()
                addresses = {str(account.get(key) or "").casefold() for key in ("mail", "userPrincipalName")}
                if settings.DOCUMENT_EMAIL_SENDER.casefold() not in addresses:
                    raise OutlookAuthError("Le compte connecté ne correspond pas à LRSTOCK_EMAIL_SENDER. Reconnectez-vous avec la bonne adresse principale.")
                _save_cache(cache)
                return
            error = data.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = min(interval + 5, 30)
                continue
            raise OutlookAuthError("La connexion a été refusée ou a expiré. Relancez connect_outlook et vérifiez les autorisations déléguées.")
        raise OutlookAuthError("Le code de connexion a expiré. Relancez connect_outlook.")
    except (requests.RequestException, ValueError, KeyError, TypeError, OSError):
        raise OutlookAuthError("La connexion Microsoft n’a pas abouti. Vérifiez l’application et la connexion Internet, puis relancez connect_outlook.")
