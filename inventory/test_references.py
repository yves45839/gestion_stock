from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Brand, Category, Customer, DocumentReferenceCounter, Product, Sale,
    SaleItem, Site, SiteAssignment, StockMovement,
)
from .views import _get_document_meta


class DocumentFixtures:
    def setUp(self):
        super().setUp()
        for prefix in ("DEV", "VTE"):
            DocumentReferenceCounter.objects.get_or_create(prefix=prefix)
        self.site = Site.objects.create(name="Reference tests")
        self.user = get_user_model().objects.create_user(username="reference-user")
        SiteAssignment.objects.create(user=self.user, site=self.site)
        self.client.force_login(self.user)
        self.product = Product.objects.create(
            sku="REF-PRODUCT", name="Produit", sale_price=Decimal("10"),
            brand=Brand.objects.create(name="Reference brand"),
            category=Category.objects.create(name="Reference category"),
        )

    def payload(self, kind="sale"):
        response = self.client.get(reverse(f"inventory:{kind}_create"))
        self.assertEqual(response.status_code, 200)
        return {
            "submission_token": response.context["sale_form"]["submission_token"].value(),
            # An old or manipulated reference must never be trusted.
            "reference": "VTE-ALREADY-USED",
            "sale_date": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "customer_name": "Reference customer",
            "amount_paid": "0",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
            "items-0-line_type": "product", "items-0-product": str(self.product.pk),
            "items-0-quantity": "2", "items-0-unit_price": "10",
        }

    def draft(self, reference="DEV-LEGACY-001"):
        sale = Sale.objects.create(reference=reference, site=self.site)
        SaleItem.objects.create(sale=sale, product=self.product, quantity=2, unit_price=10)
        return sale


class DocumentReferenceTests(DocumentFixtures, TestCase):
    def test_two_open_forms_receive_distinct_references_only_when_saved(self):
        Sale.objects.create(reference="VTE-ALREADY-USED")
        first, second = self.payload(), self.payload()
        self.assertEqual(Sale.objects.count(), 1)
        self.assertNotEqual(first["submission_token"], second["submission_token"])
        for payload in (first, second):
            self.assertEqual(self.client.post(reverse("inventory:sale_create"), payload).status_code, 302)
        sales = Sale.objects.exclude(reference="VTE-ALREADY-USED").order_by("pk")
        stem = f"VTE-{timezone.localdate():%Y%m%d}-"
        self.assertEqual(list(sales.values_list("reference", flat=True)), [stem + "001", stem + "002"])
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_resubmitting_sale_does_not_duplicate_customer_lines_or_stock(self):
        payload = self.payload()
        url = reverse("inventory:sale_create")
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(SaleItem.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_quote_conversion_keeps_original_number_and_uses_sale_number_in_stock(self):
        payload = self.payload("quote")
        url = reverse("inventory:quote_create")
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        quote = Sale.objects.get()
        original = quote.reference
        self.assertTrue(original.startswith("DEV-"))
        self.assertFalse(StockMovement.objects.exists())
        stale = Sale.objects.get(pk=quote.pk)
        quote.confirm(site=self.site, convert_quote=True)
        stale.confirm(site=self.site, convert_quote=True)
        self.assertTrue(quote.reference.startswith("VTE-"))
        self.assertEqual(quote.source_quote_reference, original)
        self.assertEqual(stale.reference, quote.reference)
        self.assertEqual(StockMovement.objects.get().document_number, quote.reference)
        self.assertEqual(_get_document_meta(quote, "quote")["reference"], original)
        self.assertEqual(_get_document_meta(quote, "invoice")["reference"], quote.reference)
        self.assertContains(self.client.get(reverse("inventory:quote_detail", args=[quote.pk])), original)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_old_quote_with_vte_prefix_is_renumbered_on_conversion(self):
        quote = self.draft("VTE-20260101-014")
        response = self.client.post(reverse("inventory:quote_confirm", args=[quote.pk]))
        self.assertEqual(response.status_code, 302)
        quote.refresh_from_db()
        self.assertEqual(quote.source_quote_reference, "VTE-20260101-014")
        self.assertNotEqual(quote.reference, quote.source_quote_reference)
        confirmed_reference = quote.reference
        self.client.post(reverse("inventory:quote_confirm", args=[quote.pk]))
        quote.refresh_from_db()
        self.assertEqual(quote.reference, confirmed_reference)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_quote_edit_preserves_reference_and_does_not_consume_a_number(self):
        quote = self.draft()
        payload = self.payload("quote")
        payload["reference"] = "DEV-TAMPERED"
        response = self.client.post(reverse("inventory:quote_edit", args=[quote.pk]), payload)
        self.assertEqual(response.status_code, 302)
        quote.refresh_from_db()
        self.assertEqual(quote.reference, "DEV-LEGACY-001")
        self.assertEqual(DocumentReferenceCounter.objects.get(pk="DEV").value, 0)

    def test_invalid_submission_does_not_reserve_a_number_and_can_be_corrected(self):
        payload = self.payload()
        payload["amount_paid"] = "9999"
        response = self.client.post(reverse("inventory:sale_create"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("amount_paid", response.context["sale_form"].errors)
        self.assertEqual(response.context["sale_form"]["submission_token"].value(), payload["submission_token"])
        self.assertEqual(DocumentReferenceCounter.objects.get(pk="VTE").value, 0)
        payload["amount_paid"] = "0"
        self.assertEqual(self.client.post(reverse("inventory:sale_create"), payload).status_code, 302)
        self.assertEqual(Sale.objects.count(), 1)

    def test_token_is_required_signed_and_scoped_to_user_and_document_type(self):
        payload = self.payload()
        url = reverse("inventory:sale_create")
        for token in ("", "invalid-signature", self.payload("quote")["submission_token"]):
            payload["submission_token"] = token
            response = self.client.post(url, payload)
            self.assertEqual(response.status_code, 200)
            self.assertIn("submission_token", response.context["sale_form"].errors)
        payload = self.payload()
        other = get_user_model().objects.create_user(username="another-user")
        self.client.force_login(other)
        response = self.client.post(url, payload)
        self.assertIn("submission_token", response.context["sale_form"].errors)
        self.assertFalse(Sale.objects.exists())

    def test_counters_reset_daily_skip_existing_references_and_rollback(self):
        today = timezone.localdate()
        DocumentReferenceCounter.objects.filter(pk="VTE").update(day=today - timedelta(days=1), value=99)
        Sale.objects.create(reference=f"VTE-{today:%Y%m%d}-001")
        self.assertEqual(DocumentReferenceCounter.next_reference("VTE"), f"VTE-{today:%Y%m%d}-002")
        self.assertEqual(DocumentReferenceCounter.next_reference("DEV"), f"DEV-{today:%Y%m%d}-001")
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                DocumentReferenceCounter.next_reference("VTE")
                raise RuntimeError("rollback")
        self.assertEqual(DocumentReferenceCounter.next_reference("VTE"), f"VTE-{today:%Y%m%d}-003")

    def test_failed_conversion_rolls_back_reference_stock_and_counter(self):
        quote = self.draft()
        with patch("inventory.models.StockMovement.objects.create", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                quote.confirm(site=self.site, convert_quote=True)
        quote.refresh_from_db()
        self.assertEqual(quote.reference, "DEV-LEGACY-001")
        self.assertEqual(quote.source_quote_reference, "")
        self.assertEqual(quote.status, Sale.Status.DRAFT)
        self.assertFalse(StockMovement.objects.exists())
        self.assertEqual(DocumentReferenceCounter.objects.get(pk="VTE").value, 0)


class ConcurrentDocumentTests(DocumentFixtures, TransactionTestCase):
    def concurrent(self, tasks):
        """Use a real SQLite file: shared in-memory SQLite ignores busy_timeout.

        Copy committed fixtures; each worker uses an independent connection.
        The normal test connection and its data remain untouched.
        """
        original = connections.databases["default"]
        barrier = Barrier(len(tasks))

        def run(task, synchronize=True):
            try:
                if synchronize:
                    barrier.wait(timeout=10)
                return task()
            finally:
                connections.close_all()

        with tempfile.TemporaryDirectory() as directory:
            if connection.vendor == "sqlite":
                filename = str(Path(directory) / "concurrent.sqlite3")
                connection.ensure_connection()
                with sqlite3.connect(filename) as target:
                    connection.connection.backup(target)
                connections.databases["default"] = {**original, "NAME": filename}
            try:
                with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
                    futures = [executor.submit(run, task) for task in tasks]
                    results = [future.result(timeout=30) for future in futures]

                    def inspect():
                        return {
                            "references": list(Sale.objects.order_by("pk").values_list("reference", flat=True)),
                            "originals": list(Sale.objects.values_list("source_quote_reference", flat=True)),
                            "movements": list(StockMovement.objects.values_list("document_number", flat=True)),
                            "customers": Customer.objects.count(),
                            "items": SaleItem.objects.count(),
                        }
                    state = executor.submit(run, inspect, False).result(timeout=30)
            finally:
                connections.databases["default"] = original
        return results, state

    def post_task(self, payload, kind="sale"):
        cookies = self.client.cookies.copy()

        def task():
            client = Client()
            client.cookies = cookies.copy()
            return client.post(reverse(f"inventory:{kind}_create"), payload).status_code
        return task

    def test_simultaneous_sales_from_two_open_forms(self):
        results, state = self.concurrent([self.post_task(self.payload()), self.post_task(self.payload())])
        self.assertEqual(results, [302, 302])
        self.assertEqual(len(set(state["references"])), 2)
        self.assertEqual(len(state["movements"]), 2)
        self.assertEqual(set(state["references"]), set(state["movements"]))

    def test_simultaneous_duplicate_sale_submission(self):
        payload = self.payload()
        results, state = self.concurrent([self.post_task(payload), self.post_task(payload)])
        self.assertEqual(results, [302, 302])
        self.assertEqual(len(state["references"]), 1)
        self.assertEqual(len(state["movements"]), 1)
        self.assertEqual(state["items"], 1)
        self.assertEqual(state["customers"], 1)

    def test_simultaneous_duplicate_quote_submission(self):
        payload = self.payload("quote")
        results, state = self.concurrent([self.post_task(payload, "quote"), self.post_task(payload, "quote")])
        self.assertEqual(results, [302, 302])
        self.assertEqual(len(state["references"]), 1)
        self.assertTrue(state["references"][0].startswith("DEV-"))
        self.assertEqual(state["movements"], [])
        self.assertEqual(state["items"], 1)

    def test_simultaneous_confirmation_of_same_quote(self):
        quote = self.draft()

        def task():
            stale = Sale.objects.get(pk=quote.pk)
            stale.confirm(site=Site.objects.get(pk=self.site.pk), convert_quote=True)
            return stale.reference

        results, state = self.concurrent([task, task])
        self.assertEqual(results[0], results[1])
        self.assertTrue(results[0].startswith("VTE-"))
        self.assertEqual(state["originals"], ["DEV-LEGACY-001"])
        self.assertEqual(state["movements"], results[:1])
