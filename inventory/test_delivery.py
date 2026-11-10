from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from .delivery.documents import batch_folder
from .delivery.providers import DeliveryError, email_send, whatsapp_send
from .models import DocumentDelivery, Sale, Site, SiteAssignment, StockMovement
from .test_references import DocumentFixtures


class DocumentDeliveryTests(DocumentFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.folder = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(DOCUMENT_DELIVERY_ENABLED=True,
            DOCUMENT_DELIVERY_SIMULATE=True, DOCUMENT_DELIVERY_ROOT=self.folder))
        self.sale = self.draft()
        self.compose_url = reverse("inventory:delivery_compose", args=[self.sale.pk, "quote"])

    def payload(self, **changes):
        return {"channels": ["email", "whatsapp"], "email": "test@example.com", "phone": "+2250700000000",
                "file_format": "pdf", "subject": "Votre devis", "message": "Bonjour, voici votre devis.",
                "whatsapp_consent": "on", **changes}

    def prepare(self, **changes):
        response = self.client.post(self.compose_url, self.payload(**changes))
        self.assertEqual(response.status_code, 302)
        return DocumentDelivery.objects.latest("created_at")

    def send(self, batch):
        return self.client.post(reverse("inventory:delivery_send", args=[batch.pk]), {"confirm": "yes"})

    def live_settings(self):
        return override_settings(DOCUMENT_DELIVERY_SIMULATE=False,
            DOCUMENT_DELIVERY_EMAIL_ALLOWLIST=["test@example.com"], DOCUMENT_DELIVERY_PHONE_ALLOWLIST=["+2250700000000"],
            DOCUMENT_MS_TENANT_ID="11111111-2222-3333-4444-555555555555",
            DOCUMENT_MS_CLIENT_ID="11111111-2222-3333-4444-666666666666", DOCUMENT_MS_CLIENT_SECRET="dummy-test-only", DOCUMENT_WA_TOKEN="dummy-test-only",
            DOCUMENT_WA_PHONE_ID="123456", DOCUMENT_WA_VERSION="v23.0", DOCUMENT_WA_PDF_TEMPLATE="lrstock_document",
            DOCUMENT_WA_IMAGE_TEMPLATE="lrstock_image")

    def test_disabled_feature_and_authentication(self):
        with override_settings(DOCUMENT_DELIVERY_ENABLED=False):
            self.assertEqual(self.client.get(self.compose_url).status_code, 404)
            self.assertNotContains(self.client.get(reverse("inventory:quote_detail", args=[self.sale.pk])), "Préparer un envoi")
        self.client.logout()
        self.assertEqual(self.client.get(self.compose_url).status_code, 302)
        self.assertEqual(self.client.post(self.compose_url, self.payload()).status_code, 302)
        self.assertFalse(DocumentDelivery.objects.exists())

    def test_prepare_is_a_real_pdf_snapshot_and_does_not_send_or_change_stock(self):
        with patch("inventory.delivery.providers.requests.post") as api, patch("inventory.delivery.views.email_send") as smtp:
            batch = self.prepare()
            api.assert_not_called()
            smtp.assert_not_called()
        self.assertEqual(batch.status, "ready")
        content = (batch_folder(batch) / batch.attachments[0]["name"]).read_bytes()
        text = "".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)
        self.assertIn("DEV-LEGACY-001", text)
        self.assertIn("DEVIS", text)
        self.assertEqual(Sale.objects.get(pk=self.sale.pk).status, Sale.Status.DRAFT)
        self.assertFalse(StockMovement.objects.exists())

    def test_simulation_both_channels_without_any_external_call(self):
        batch = self.prepare()
        with patch("inventory.delivery.views.email_send") as smtp, patch("inventory.delivery.views.whatsapp_send") as api:
            self.send(batch)
            smtp.assert_not_called()
            api.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual([result["status"] for result in batch.results], ["simulated", "simulated"])
        self.assertEqual(batch.status, "completed")

    def test_download_is_private_and_restricted_to_author(self):
        batch = self.prepare()
        url = reverse("inventory:delivery_attachment", args=[batch.pk, 0])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Content-Type"], "application/pdf")
        response.close()
        other = get_user_model().objects.create_user(username="another-delivery-user")
        SiteAssignment.objects.create(user=other, site=self.site)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse("inventory:delivery_detail", args=[batch.pk])).status_code, 404)
        self.assertEqual(self.send(batch).status_code, 404)

    def test_cross_site_documents_and_reports_are_denied(self):
        other = Site.objects.create(name="Other delivery site")
        self.sale.site = other
        self.sale.save(update_fields=["site"])
        self.assertEqual(self.client.get(self.compose_url).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory:delivery_report"), {"site": other.pk}).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory:delivery_report")).status_code, 200)

    def test_invoice_requires_confirmation_and_quotes_are_pdf_only(self):
        self.assertEqual(self.client.get(reverse("inventory:delivery_compose", args=[self.sale.pk, "invoice"])).status_code, 404)
        response = self.client.post(self.compose_url, self.payload(file_format="png"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DocumentDelivery.objects.exists())
        self.sale.confirm(site=self.site)
        self.assertEqual(self.client.post(reverse("inventory:delivery_compose", args=[self.sale.pk, "invoice"]), self.payload()).status_code, 302)

    def test_validation_needs_recipient_consent_and_international_phone(self):
        for data in [self.payload(email=""), self.payload(phone="0700000000"), self.payload(whatsapp_consent=""), self.payload(channels=[])]:
            response = self.client.post(self.compose_url, data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertFalse(DocumentDelivery.objects.exists())
        batch = self.prepare(phone="00225 07 00 00 00 00")
        self.assertEqual(batch.destinations["phone"], "+2250700000000")

    def test_report_images_include_every_page(self):
        from .views import HTML
        pdf = HTML(string='<style>@page{size:A4}</style>' + '<p style="page-break-after:always">Page</p>' * 2 + '<p>Last page</p>').write_pdf()
        with patch("inventory.delivery.documents.render_pdf", return_value=pdf):
            response = self.client.post(reverse("inventory:delivery_report"), self.payload(file_format="png"))
        self.assertEqual(response.status_code, 302)
        batch = DocumentDelivery.objects.latest("created_at")
        self.assertEqual(len(batch.attachments), 3)
        for item in batch.attachments:
            self.assertEqual(item["mimetype"], "image/png")
            self.assertTrue((batch_folder(batch) / item["name"]).read_bytes().startswith(b"\x89PNG"))
        self.send(batch)
        batch.refresh_from_db()
        self.assertEqual(len(batch.results), 4)  # one email with all pages, three WhatsApp messages

    def test_report_filters_site_and_period_and_freezes_file(self):
        self.sale.status = Sale.Status.CONFIRMED
        self.sale.save(update_fields=["status"])
        self.client.post(reverse("inventory:delivery_report") + "?period=year", self.payload(channels=["email"]))
        batch = DocumentDelivery.objects.latest("created_at")
        self.assertEqual(batch.site, self.site)
        self.assertEqual(batch.report_filters["period"], "year")
        content = (batch_folder(batch) / batch.attachments[0]["name"]).read_bytes()
        text = "".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)
        self.assertIn(self.sale.reference, text)
        self.assertIn(self.site.name, text)
        self.sale.delete()
        # Existing delivery protects its referenced sale only for sales documents; report is a snapshot.
        self.assertEqual((batch_folder(batch) / batch.attachments[0]["name"]).read_bytes(), content)

    def test_send_requires_explicit_confirmation_and_post(self):
        batch = self.prepare()
        url = reverse("inventory:delivery_send", args=[batch.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {})
        batch.refresh_from_db()
        self.assertEqual(batch.status, "ready")

    def test_live_sends_are_allowlisted_and_duplicate_confirmation_does_not_resend(self):
        with self.live_settings():
            batch = self.prepare()
            with patch("inventory.delivery.views.email_send", return_value="email-id") as smtp, patch("inventory.delivery.views.whatsapp_send", return_value="wamid.test") as api:
                self.send(batch)
                self.send(batch)
                smtp.assert_called_once()
                api.assert_called_once()
        batch.refresh_from_db()
        self.assertTrue(all(result["status"] == "accepted" for result in batch.results))

    def test_live_recipient_not_allowlisted_never_contacts_any_provider(self):
        with self.live_settings():
            batch = self.prepare(email="not-allowed@example.com")
            with patch("inventory.delivery.views.email_send") as smtp, patch("inventory.delivery.views.whatsapp_send") as api:
                self.send(batch)
                smtp.assert_not_called()
                api.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status, "ready")

    def test_partial_failure_retains_email_success_and_uncertain_whatsapp(self):
        with self.live_settings():
            batch = self.prepare()
            with patch("inventory.delivery.views.email_send", return_value="email-id"), patch("inventory.delivery.views.whatsapp_send", side_effect=DeliveryError("Confirmation absente", uncertain=True)):
                self.send(batch)
        batch.refresh_from_db()
        self.assertEqual([result["status"] for result in batch.results], ["accepted", "uncertain"])

    def test_mode_change_never_converts_simulation_to_live_send(self):
        batch = self.prepare()
        with self.live_settings(), patch("inventory.delivery.views.email_send") as smtp:
            self.send(batch)
            smtp.assert_not_called()
        batch.refresh_from_db()
        self.assertEqual(batch.status, "ready")

    def test_microsoft_adapter_uses_mailbox_and_binary_attachments(self):
        import base64
        batch = self.prepare(channels=["email"])
        token = Mock(status_code=200)
        token.json.return_value = {"access_token": "dummy-test-token"}
        accepted = Mock(status_code=202, headers={"request-id": "graph-request-id"})
        with self.live_settings(), patch("inventory.delivery.providers.requests.post", side_effect=[token, accepted]) as api:
            self.assertEqual(email_send(batch), "graph-request-id")
        args = api.call_args_list[1]
        self.assertEqual(args.args[0], "https://graph.microsoft.com/v1.0/users/info@label-ci.com/sendMail")
        message = args.kwargs["json"]["message"]
        self.assertEqual(message["from"]["emailAddress"]["address"], "info@label-ci.com")
        self.assertEqual(message["from"]["emailAddress"]["name"], "LrStock")
        self.assertTrue(base64.b64decode(message["attachments"][0]["contentBytes"]).startswith(b"%PDF"))
        self.assertEqual(message["toRecipients"][0]["emailAddress"]["address"], "test@example.com")

    def test_whatsapp_adapter_uploads_privately_then_uses_approved_template(self):
        batch = self.prepare(channels=["whatsapp"])
        upload = Mock(status_code=200)
        upload.json.return_value = {"id": "media-123"}
        send = Mock(status_code=200)
        send.json.return_value = {"messages": [{"id": "wamid.123"}]}
        with self.live_settings(), patch("inventory.delivery.providers.requests.post", side_effect=[upload, send]) as api:
            self.assertEqual(whatsapp_send(batch, batch.attachments[0]), "wamid.123")
            payload = api.call_args_list[1].kwargs["json"]
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["to"], "2250700000000")
        self.assertEqual(payload["template"]["components"][0]["parameters"][0]["document"]["id"], "media-123")
        self.assertNotIn("link", str(payload))

    def test_missing_attachment_blocks_even_simulation(self):
        batch = self.prepare()
        (batch_folder(batch) / batch.attachments[0]["name"]).unlink()
        self.send(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "ready")
        self.assertFalse(batch.results)

    def test_microsoft_timeout_after_send_is_uncertain_and_not_retried(self):
        import requests
        batch = self.prepare(channels=["email"])
        token = Mock(status_code=200)
        token.json.return_value = {"access_token": "dummy-token"}
        with self.live_settings(), patch("inventory.delivery.providers.requests.post", side_effect=[token, requests.Timeout()]):
            with self.assertRaises(DeliveryError) as error:
                email_send(batch)
        self.assertTrue(error.exception.uncertain)

    def test_whatsapp_upload_failure_never_attempts_message(self):
        batch = self.prepare(channels=["whatsapp"])
        failure = Mock(status_code=403)
        with self.live_settings(), patch("inventory.delivery.providers.requests.post", return_value=failure) as api:
            with self.assertRaises(DeliveryError) as error:
                whatsapp_send(batch, batch.attachments[0])
            api.assert_called_once()
        self.assertFalse(error.exception.uncertain)

    def test_sending_request_without_csrf_is_denied(self):
        from django.test import Client
        batch = self.prepare()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(reverse("inventory:delivery_send", args=[batch.pk]), {"confirm": "yes"}).status_code, 403)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "ready")

    def test_report_image_uses_image_template_and_not_document_header(self):
        response = self.client.post(reverse("inventory:delivery_report"), self.payload(file_format="png", channels=["whatsapp"]))
        self.assertEqual(response.status_code, 302)
        batch = DocumentDelivery.objects.latest("created_at")
        upload = Mock(status_code=200)
        upload.json.return_value = {"id": "image-123"}
        sent = Mock(status_code=200)
        sent.json.return_value = {"messages": [{"id": "wamid.image"}]}
        with self.live_settings(), patch("inventory.delivery.providers.requests.post", side_effect=[upload, sent]) as api:
            whatsapp_send(batch, batch.attachments[0])
            payload = api.call_args_list[1].kwargs["json"]
        self.assertEqual(payload["template"]["name"], "lrstock_image")
        self.assertEqual(payload["template"]["components"][0]["parameters"][0], {"type": "image", "image": {"id": "image-123"}})
