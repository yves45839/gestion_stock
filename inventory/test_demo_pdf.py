from io import BytesIO
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from .demo_pdf import DemoPDFError, render_demo_pdf, should_use_demo_pdf
from .models import DocumentDelivery
from .test_references import DocumentFixtures


class DemoPDFGuardTests(SimpleTestCase):
    @override_settings(IS_SALES_DEMO=False)
    def test_production_cannot_use_demo_fallback(self):
        with patch("inventory.demo_pdf.subprocess.run") as run:
            with self.assertRaises(DemoPDFError):
                render_demo_pdf("<p>test</p>")
            run.assert_not_called()
        self.assertFalse(should_use_demo_pdf(None))

    @override_settings(IS_SALES_DEMO=True)
    def test_missing_browser_has_actionable_error(self):
        with patch("inventory.demo_pdf.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"missing browser")):
            with self.assertRaisesRegex(DemoPDFError, "start_demo.py"):
                render_demo_pdf("<p>test</p>")


@skipUnless(shutil.which("chromium"), "Local Chromium needed for fallback integration checks")
class DemoPDFIntegrationTests(DocumentFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(IS_SALES_DEMO=True, DOCUMENT_DELIVERY_ENABLED=True,
            DOCUMENT_DELIVERY_SIMULATE=True, DOCUMENT_DELIVERY_ROOT=self.enterContext(TemporaryDirectory())))
        self.enterContext(patch.dict(os.environ, {"LRSTOCK_DEMO_CHROMIUM_EXECUTABLE": shutil.which("chromium")}))
        self.enterContext(patch("inventory.views.HTML", None))
        self.sale = self.draft()

    def test_missing_native_library_still_prepares_quote_pdf_and_report_png(self):
        payload = {"channels": ["email"], "email": "test@example.com", "file_format": "pdf",
                   "subject": "Test", "message": "Test PDF Windows"}
        response = self.client.post(reverse("inventory:delivery_compose", args=[self.sale.pk, "quote"]), payload)
        self.assertEqual(response.status_code, 302)
        batch = DocumentDelivery.objects.latest("created_at")
        from .delivery.documents import batch_folder
        text = "".join(page.extract_text() for page in PdfReader(batch_folder(batch) / batch.attachments[0]["name"]).pages)
        self.assertIn(self.sale.reference, text)
        response = self.client.post(reverse("inventory:delivery_report"), {**payload, "file_format": "png"})
        self.assertEqual(response.status_code, 302)
        batch = DocumentDelivery.objects.latest("created_at")
        self.assertTrue((batch_folder(batch) / batch.attachments[0]["name"]).read_bytes().startswith(b"\x89PNG"))

    def test_existing_document_and_report_exports_also_use_demo_fallback(self):
        for url in [reverse("inventory:sale_document_pdf", args=[self.sale.pk, "quote"]), reverse("inventory:analytics_sales_pdf")]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_launcher_forced_fallback_works_even_if_native_import_succeeded(self):
        with patch("inventory.views.HTML", object()), patch.dict(os.environ, {"LRSTOCK_DEMO_PDF_ENGINE": "chromium"}):
            response = self.client.get(reverse("inventory:sale_document_pdf", args=[self.sale.pk, "quote"]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))
