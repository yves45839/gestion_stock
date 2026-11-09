from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import Customer, Sale, SaleItem
from .test_references import DocumentFixtures


class CreditLimitTests(DocumentFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.customer = Customer.objects.create(name="Client crédit", credit_limit=Decimal("100"))

    def invoice(self, amount, paid="0", **changes):
        sale = Sale.objects.create(reference=f"CREDIT-{Sale.objects.count()}", customer=self.customer, status=Sale.Status.CONFIRMED,
                                   amount_paid=Decimal(paid), **changes)
        SaleItem.objects.create(sale=sale, product=self.product, quantity=1, unit_price=Decimal(amount))
        return sale

    def test_alert_thresholds_and_zero_limit(self):
        for amount, state in [("0", None), ("79.99", None), ("80", "near"),
                              ("99.99", "near"), ("100", "reached"), ("100.01", "exceeded")]:
            with self.subTest(amount=amount):
                alert = self.customer.credit_alert_for(Decimal(amount))
                self.assertEqual(alert["state"] if alert else None, state)
        self.customer.credit_limit = Decimal("0")
        self.assertIsNone(self.customer.credit_alert_for(Decimal("0")))
        self.assertEqual(self.customer.credit_alert_for(Decimal("0.01"))["state"], "exceeded")

    def test_exposure_uses_discounted_unpaid_invoices_and_refreshes_after_payment(self):
        invoice = self.invoice("100", paid="10", discount_value=Decimal("10"))
        draft = Sale.objects.create(reference="DEV-CREDIT", customer=self.customer)
        SaleItem.objects.create(sale=draft, product=self.product, quantity=1, unit_price=9999)
        self.invoice("200", paid="200")
        self.assertEqual(self.customer.credit_alert["outstanding"], Decimal("80"))
        invoice.amount_paid = Decimal("20.01")
        invoice.save(update_fields=["amount_paid"])
        self.assertIsNone(self.customer.credit_alert)

    def test_returns_reduce_credit_exposure(self):
        invoice = self.invoice("100")
        self.assertEqual(self.customer.credit_alert["state"], "reached")
        item = invoice.items.get()
        item.returned_quantity = 1
        item.save(update_fields=["returned_quantity"])
        self.assertIsNone(self.customer.credit_alert)

    def test_customer_pages_show_current_status_and_editor_exposes_limit(self):
        self.invoice("80")
        for url in (reverse("inventory:customer_list"), self.customer.get_absolute_url()):
            self.assertContains(self.client.get(url), 'data-credit-state="near"')
        for kind in ("sale", "quote"):
            response = self.client.get(reverse(f"inventory:{kind}_create"))
            data = next(c for c in response.context["customer_dataset"] if c["id"] == self.customer.pk)
            self.assertEqual(Decimal(data["credit_limit"]), Decimal("100"))
            self.assertEqual(Decimal(data["balance"]), Decimal("80"))
            self.assertEqual(data["credit_warning_percent"], 80)

    def test_customer_list_credit_alerts_do_not_query_each_customer_separately(self):
        self.invoice("80")
        Customer.objects.create(name="Autre client")
        with self.assertNumQueries(3):
            customers = list(Customer.objects.with_balance().with_credit_invoices())
            alerts = [customer.credit_alert for customer in customers]
        self.assertEqual(sum(bool(a) for a in alerts), 1)

    def test_saved_sale_warns_without_blocking_when_limit_reached(self):
        self.invoice("79.99")
        payload = self.payload()
        payload.update({"customer": self.customer.pk, "items-0-quantity": "1",
                        "items-0-unit_price": "20.01", "amount_paid": "0"})
        response = self.client.post(reverse("inventory:sale_create"), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.customer.credit_alert["state"], "reached")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Plafond de crédit atteint" in m for m in messages))

    def test_paid_sale_does_not_trigger_projected_debt(self):
        self.invoice("79.99")
        payload = self.payload()
        payload.update({"customer": self.customer.pk, "items-0-unit_price": "100",
                        "payment_mode": "auto"})
        response = self.client.post(reverse("inventory:sale_create"), payload)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.customer.credit_alert)
        self.assertFalse(any("Plafond de crédit" in str(m) for m in get_messages(response.wsgi_request)))

    def test_quote_projection_does_not_count_as_debt_until_conversion(self):
        self.invoice("70")
        quote = Sale.objects.create(reference="DEV-CREDIT", customer=self.customer, site=self.site)
        SaleItem.objects.create(sale=quote, product=self.product, quantity=1, unit_price=30)
        response = self.client.get(reverse("inventory:quote_detail", args=[quote.pk]))
        self.assertEqual(response.context["credit_projection"]["state"], "reached")
        self.assertContains(response, "Si ce devis est confirmé sans règlement")
        self.assertIsNone(self.customer.credit_alert)
        response = self.client.post(reverse("inventory:quote_confirm", args=[quote.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("Plafond de crédit atteint" in str(m) for m in get_messages(response.wsgi_request)))
