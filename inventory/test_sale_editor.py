from decimal import Decimal
from io import BytesIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from .models import Customer, CustomerAccountEntry, DocumentReferenceCounter, Sale, SaleItem, StockMovement, Version
from .test_references import DocumentFixtures
from .views import _build_document_context, _get_document_meta


class SaleEditorTests(DocumentFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.customer = Customer.objects.create(name="Client impayés")
        for reference, price, paid in [("OLD-1", "70000", "10000"), ("OLD-2", "30000", "7500"), ("PAID", "1000", "1000")]:
            invoice = Sale.objects.create(reference=reference, customer=self.customer, amount_paid=Decimal(paid), status=Sale.Status.CONFIRMED)
            SaleItem.objects.create(sale=invoice, product=self.product, quantity=1, unit_price=Decimal(price))
        draft = Sale.objects.create(reference="DEV-EXCLUDED", customer=self.customer)
        SaleItem.objects.create(sale=draft, product=self.product, quantity=1, unit_price=999999)

    def editor_payload(self, kind="sale", **changes):
        payload = self.payload(kind)
        payload.update({"customer": str(self.customer.pk), "customer_name": "", "items-0-quantity": "1",
                        "items-0-unit_price": "37500", "discount_type": "percent", "discount_value": "10",
                        "amount_paid": "20000", "show_customer_balance": "on"})
        payload.update(changes)
        return payload

    def post_sale(self, payload=None):
        payload = payload or self.editor_payload()
        response = self.client.post(reverse("inventory:sale_create"), payload)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        return Sale.objects.exclude(reference__in=["OLD-1", "OLD-2", "PAID", "DEV-EXCLUDED"]).get()

    def test_percent_discount_and_partial_payment_update_invoice_and_account(self):
        sale = self.post_sale()
        self.assertEqual(sale.subtotal_amount, Decimal("37500"))
        self.assertEqual(sale.discount_amount, Decimal("3750"))
        self.assertEqual(sale.total_amount, Decimal("33750"))
        self.assertEqual(sale.remaining_amount, Decimal("13750"))
        self.assertEqual(sale.previous_customer_balance, Decimal("82500"))
        self.assertEqual(sale.document_customer_balance, Decimal("96250"))
        self.assertEqual(self.customer.unpaid_invoice_balance(), Decimal("96250"))
        debit = sale.account_entries.get(entry_type=CustomerAccountEntry.EntryType.DEBIT)
        self.assertEqual(debit.amount, sale.total_amount)

    def test_fixed_discount_produces_same_net(self):
        sale = self.post_sale(self.editor_payload(discount_type="fixed", discount_value="3750"))
        self.assertEqual(sale.total_amount, Decimal("33750"))

    def test_decimal_discount_rounds_half_up(self):
        sale = self.post_sale(self.editor_payload(**{"items-0-unit_price": "1.05", "amount_paid": "0"}))
        self.assertEqual(sale.discount_amount, Decimal("0.11"))
        self.assertEqual(sale.total_amount, Decimal("0.94"))

    def test_invalid_discounts_and_overpayments_never_write(self):
        for changes, error in [({"discount_value": "101"}, "discount_value"),
                               ({"discount_value": "-1"}, "discount_value"),
                               ({"discount_type": "fixed", "discount_value": "40000"}, "discount_value"),
                               ({"amount_paid": "35000"}, "amount_paid")]:
            before = Sale.objects.count()
            response = self.client.post(reverse("inventory:sale_create"), self.editor_payload(**changes))
            self.assertEqual(response.status_code, 200)
            self.assertIn(error, response.context["sale_form"].errors)
            self.assertEqual(Sale.objects.count(), before)
            self.assertFalse(StockMovement.objects.exists())

    def test_free_sale_is_allowed_with_zero_payment(self):
        sale = self.post_sale(self.editor_payload(discount_value="100", amount_paid="0"))
        self.assertEqual(sale.total_amount, 0)
        self.assertEqual(sale.remaining_amount, 0)

    def test_draft_does_not_change_balance_and_conversion_refreshes_snapshot(self):
        response = self.client.post(reverse("inventory:quote_create"), self.editor_payload("quote"))
        self.assertEqual(response.status_code, 302)
        quote = Sale.objects.filter(submission_key__isnull=False).get()
        self.assertEqual(quote.amount_paid, 0)
        self.assertEqual(self.customer.unpaid_invoice_balance(), Decimal("82500"))
        self.assertFalse(StockMovement.objects.exists())
        old = Sale.objects.get(reference="OLD-1")
        old.amount_paid = Decimal("20000")
        old.save(update_fields=["amount_paid"])
        self.client.post(reverse("inventory:quote_confirm", args=[quote.pk]))
        quote.refresh_from_db()
        self.assertEqual(quote.previous_customer_balance, Decimal("72500"))
        self.assertEqual(quote.discount_value, 10)
        self.assertEqual(quote.total_amount, Decimal("33750"))
        self.assertEqual(self.customer.unpaid_invoice_balance(), Decimal("106250"))

    def test_preview_is_side_effect_free(self):
        before = [model.objects.count() for model in (Sale, SaleItem, Customer, StockMovement, CustomerAccountEntry, Version)]
        counter = DocumentReferenceCounter.objects.get(pk="VTE").value
        response = self.client.post(reverse("inventory:draft_document_preview"), self.editor_payload(document_kind="sale"))
        self.assertContains(response, "APERÇU — document non enregistré")
        self.assertEqual(response.context["total_amount"], Decimal("33750"))
        self.assertEqual(response.context["customer_document_balance"], Decimal("96250"))
        self.assertEqual(before, [model.objects.count() for model in (Sale, SaleItem, Customer, StockMovement, CustomerAccountEntry, Version)])
        self.assertEqual(DocumentReferenceCounter.objects.get(pk="VTE").value, counter)

    def test_preview_rejects_invalid_amount_without_saving(self):
        response = self.client.post(reverse("inventory:draft_document_preview"), self.editor_payload(document_kind="sale", discount_value="101"))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(submission_key__isnull=False).exists())

    def test_pdf_shows_discount_and_balance_only_when_requested(self):
        sale = self.post_sale()
        url = reverse("inventory:sale_document_pdf", args=[sale.pk, "invoice"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        text = ''.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        self.assertIn("Remise", text)
        self.assertIn("Solde antérieur", text)
        self.assertIn("96250", text.replace(" ", "").replace("\u202f", "").replace("\xa0", ""))
        sale.show_customer_balance = False
        sale.save(update_fields=["show_customer_balance"])
        response = self.client.get(url)
        text = ''.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        self.assertNotIn("Solde antérieur", text)
        self.assertNotIn("Solde client", text)
        self.assertIn("Reste sur cette facture", text)

    def test_legacy_documents_keep_unchanged_totals_and_hidden_balance(self):
        sale = Sale.objects.get(reference="OLD-1")
        self.assertEqual(sale.total_amount, Decimal("70000"))
        self.assertFalse(sale.show_customer_balance)

    def test_fixed_discount_is_prorated_after_partial_return(self):
        sale = self.post_sale(self.editor_payload(discount_type="fixed", discount_value="100", amount_paid="0",
            **{"items-0-unit_price": "100", "items-0-quantity": "10"}))
        item = sale.items.get()
        item.record_return(2, site=self.site)
        sale.refresh_from_db()
        self.assertEqual(sale.discount_amount, Decimal("80"))
        self.assertEqual(sale.total_amount, Decimal("720"))
        self.assertEqual(sale.returned_amount, Decimal("180"))
        self.assertEqual(sale.account_entries.get(entry_type="debit").amount, Decimal("720"))

    def test_editor_preserves_all_navigation_and_exposes_correct_stock(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("inventory:sale_create"))
        for name in ["Analyses", "Mouvements", "Inventaire physique", "Valorisation", "IA", "Import", "Paiement", "Aperçu"]:
            self.assertContains(response, name)
        customer = next(c for c in response.context["customer_dataset"] if c["id"] == self.customer.pk)
        self.assertEqual(Decimal(customer["balance"]), Decimal("82500"))

    def test_dashboard_and_analytics_use_discounted_sales_amounts(self):
        sale = self.post_sale()
        response = self.client.get(reverse("inventory:analytics"))
        self.assertEqual(response.status_code, 200)
        # Filter is the assigned site; old invoices have no site and are excluded.
        self.assertEqual(response.context["sales_totals"]["total_amount"], sale.total_amount)
        self.assertEqual(self.client.get(reverse("inventory:dashboard")).status_code, 200)

    def test_demo_seed_refuses_normal_settings(self):
        with self.assertRaises(CommandError):
            call_command("prepare_sales_demo", verbosity=0)
