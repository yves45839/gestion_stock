import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from .delivery.outlook_auth import OutlookAuthError, access_token, connect, connection_ready


def response(status=200, data=None):
    result = Mock(status_code=status)
    result.json.return_value = data or {}
    return result


class OutlookPersonalAuthTests(SimpleTestCase):
    def setUp(self):
        self.folder = self.enterContext(TemporaryDirectory())
        self.cache = Path(self.folder) / "auth.json"
        self.enterContext(override_settings(IS_SALES_DEMO=True, DOCUMENT_MS_AUTH_MODE="personal",
            DOCUMENT_MS_CLIENT_ID="test-public-client", DOCUMENT_MS_AUTH_CACHE=self.cache,
            DOCUMENT_EMAIL_SENDER="person@outlook.com"))

    def write_cache(self, **changes):
        data = {"client_id": "test-public-client", "account": "person@outlook.com",
                "access_token": "dummy-access", "refresh_token": "dummy-refresh", "expires_at": time.time() + 3600, **changes}
        self.cache.write_text(json.dumps(data))

    def test_no_connection_and_cache_bound_to_account_and_app(self):
        self.assertFalse(connection_ready())
        self.write_cache()
        self.assertTrue(connection_ready())
        with override_settings(DOCUMENT_EMAIL_SENDER="other@outlook.com"):
            self.assertFalse(connection_ready())
        with override_settings(DOCUMENT_MS_CLIENT_ID="different-app"):
            self.assertFalse(connection_ready())

    def test_existing_token_never_contacts_microsoft(self):
        self.write_cache()
        with patch("inventory.delivery.outlook_auth.requests.post") as post:
            self.assertEqual(access_token(), "dummy-access")
            post.assert_not_called()

    def test_expired_token_is_refreshed_without_a_client_secret(self):
        self.write_cache(expires_at=0)
        updated = response(data={"access_token": "renewed-access", "refresh_token": "renewed-refresh", "expires_in": 3600, "scope": "Mail.Send User.Read"})
        with patch("inventory.delivery.outlook_auth.requests.post", return_value=updated) as post:
            self.assertEqual(access_token(), "renewed-access")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertNotIn("client_secret", post.call_args.kwargs["data"])
        self.assertEqual(json.loads(self.cache.read_text())["refresh_token"], "renewed-refresh")

    def test_missing_or_revoked_token_has_reconnect_instruction(self):
        with self.assertRaisesRegex(OutlookAuthError, "connect_outlook"):
            access_token()
        self.write_cache(expires_at=0)
        with patch("inventory.delivery.outlook_auth.requests.post", return_value=response(400)):
            with self.assertRaisesRegex(OutlookAuthError, "connect_outlook"):
                access_token()

    def test_device_login_checks_identity_and_saves_without_sending_email(self):
        device = response(data={"device_code": "private-device-code", "user_code": "TEST-CODE", "expires_in": 900})
        pending = response(400, {"error": "authorization_pending"})
        token = response(data={"access_token": "dummy-access", "refresh_token": "dummy-refresh", "scope": "Mail.Send User.Read"})
        profile = response(data={"mail": "person@outlook.com", "userPrincipalName": "person@outlook.com"})
        shown = []
        with patch("inventory.delivery.outlook_auth.requests.post", side_effect=[device, pending, token]) as post, patch("inventory.delivery.outlook_auth.requests.get", return_value=profile):
            connect(shown.append, pause=lambda _: None, monotonic=lambda: 0)
        self.assertTrue(connection_ready())
        self.assertTrue(any("TEST-CODE" in value for value in shown))
        self.assertFalse(any("dummy-refresh" in value or "private-device-code" in value for value in shown))
        self.assertTrue(all("sendMail" not in call.args[0] for call in post.call_args_list))
        if os.name != "nt":
            self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_wrong_connected_account_never_saves_authorization(self):
        device = response(data={"device_code": "code", "user_code": "USER-CODE", "expires_in": 900})
        token = response(data={"access_token": "dummy-access", "scope": "Mail.Send"})
        with patch("inventory.delivery.outlook_auth.requests.post", side_effect=[device, token]), patch("inventory.delivery.outlook_auth.requests.get", return_value=response(data={"mail": "other@outlook.com"})):
            with self.assertRaisesRegex(OutlookAuthError, "ne correspond pas"):
                connect(lambda _: None, pause=lambda _: None, monotonic=lambda: 0)
        self.assertFalse(self.cache.exists())

    def test_denied_consent_does_not_save_tokens(self):
        device = response(data={"device_code": "code", "user_code": "USER-CODE", "expires_in": 900})
        with patch("inventory.delivery.outlook_auth.requests.post", side_effect=[device, response(400, {"error": "authorization_declined"})]):
            with self.assertRaises(OutlookAuthError):
                connect(lambda _: None, pause=lambda _: None, monotonic=lambda: 0)
        self.assertFalse(self.cache.exists())

    def test_no_mail_send_scope_does_not_save_tokens(self):
        device = response(data={"device_code": "code", "user_code": "USER-CODE", "expires_in": 900})
        token = response(data={"access_token": "dummy-access", "scope": "User.Read"})
        with patch("inventory.delivery.outlook_auth.requests.post", side_effect=[device, token]):
            with self.assertRaisesRegex(OutlookAuthError, "Mail.Send"):
                connect(lambda _: None, pause=lambda _: None, monotonic=lambda: 0)
        self.assertFalse(self.cache.exists())

    @override_settings(IS_SALES_DEMO=False)
    def test_personal_connection_is_disabled_outside_demo(self):
        with patch("inventory.delivery.outlook_auth.requests.post") as post:
            with self.assertRaises(OutlookAuthError):
                access_token()
            with self.assertRaises(OutlookAuthError):
                connect(lambda _: None)
            post.assert_not_called()

    def test_cli_never_sends_a_message(self):
        from io import StringIO
        output = StringIO()
        with patch("inventory.management.commands.connect_outlook.connect") as connection:
            call_command("connect_outlook", stdout=output)
            connection.assert_called_once()
        self.assertIn("Aucun email envoyé", output.getvalue())

    def test_expired_device_code_is_not_polled_forever(self):
        device = response(data={"device_code": "code", "user_code": "USER-CODE", "expires_in": 900})
        ticks = iter([0, 901])
        with patch("inventory.delivery.outlook_auth.requests.post", return_value=device) as post:
            with self.assertRaisesRegex(OutlookAuthError, "expiré"):
                connect(lambda _: None, pause=lambda _: None, monotonic=lambda: next(ticks))
        post.assert_called_once()
        self.assertFalse(self.cache.exists())

    def test_corrupted_cache_is_treated_as_disconnected(self):
        self.cache.write_text("not json")
        self.assertFalse(connection_ready())
        self.write_cache(expires_at="invalid expiry")
        self.assertFalse(connection_ready())
