from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Customer, Sale
from .test_references import DocumentFixtures


class CustomerReviewTests(DocumentFixtures, TestCase):
    def pending_customer(self, name="Client à vérifier", user=None):
        return Customer.objects.create(
            name=name, needs_review=True, review_requested_by=user or self.user,
        )

    def customer_payload(self, customer, **changes):
        return {"reference": customer.reference, "name": customer.name,
                "credit_limit": "0", "phone": "0700000000", **changes}

    def test_sale_and_quote_creation_notify_creator_only_after_save(self):
        for kind in ("sale", "quote"):
            with self.subTest(kind=kind):
                payload = self.payload(kind)
                payload["customer_name"] = f"Nouveau client {kind}"
                preview = self.client.post(reverse("inventory:draft_document_preview"),
                                           {**payload, "document_kind": kind})
                self.assertEqual(preview.status_code, 200)
                self.assertFalse(Customer.objects.filter(name=payload["customer_name"]).exists())
                self.assertEqual(self.client.post(reverse(f"inventory:{kind}_create"), payload).status_code, 302)
                customer = Customer.objects.get(name=payload["customer_name"])
                self.assertTrue(customer.needs_review)
                self.assertEqual(customer.review_requested_by, self.user)
                self.client.post(reverse(f"inventory:{kind}_create"), payload)
                self.assertEqual(Customer.objects.filter(name=payload["customer_name"]).count(), 1)
        response = self.client.get(reverse("inventory:customer_list"))
        self.assertEqual(response.context["nav_customers_to_review"], 2)
        self.assertContains(response, 'class="customer-notification"')

    def test_badge_and_review_filter_are_personal_and_searchable(self):
        mine = self.pending_customer()
        other_user = get_user_model().objects.create_user(username="other-reviewer")
        self.pending_customer("Autre client", user=other_user)
        Customer.objects.create(name="Ancien client")
        response = self.client.get(reverse("inventory:customer_list"), {"review": "1", "q": "vérifier"})
        self.assertEqual(response.context["nav_customers_to_review"], 1)
        self.assertEqual(list(response.context["customers"]), [mine])
        self.assertContains(response, reverse("inventory:customer_update", args=[mine.pk]))
        self.assertContains(response, 'name="review" value="1"')
        self.client.force_login(other_user)
        response = self.client.get(reverse("inventory:customer_list"), {"review": "1"})
        self.assertEqual(response.context["nav_customers_to_review"], 1)
        self.assertEqual([c.name for c in response.context["customers"]], ["Autre client"])

    def test_opening_or_saving_without_confirmation_keeps_reminder(self):
        customer = self.pending_customer()
        for route in ("customer_update", "customer_detail"):
            response = self.client.get(reverse(f"inventory:{route}", args=[customer.pk]))
            self.assertContains(response, 'name="information_verified"')
        self.client.post(reverse("inventory:customer_update", args=[customer.pk]), self.customer_payload(customer))
        customer.refresh_from_db()
        self.assertTrue(customer.needs_review)
        self.assertEqual(customer.phone, "0700000000")

    def test_both_customer_edit_forms_clear_reminder_after_valid_confirmation(self):
        for route in ("customer_update", "customer_detail"):
            with self.subTest(route=route):
                customer = self.pending_customer(name=route)
                url = reverse(f"inventory:{route}", args=[customer.pk])
                payload = self.customer_payload(customer, information_verified="on", form_type="customer")
                invalid = self.client.post(url, {**payload, "email": "invalide"})
                self.assertEqual(invalid.status_code, 200)
                customer.refresh_from_db()
                self.assertTrue(customer.needs_review)
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                customer.refresh_from_db()
                self.assertFalse(customer.needs_review)
                response = self.client.get(reverse("inventory:customer_list"))
                self.assertEqual(response.context["nav_customers_to_review"], 0)
                self.assertNotContains(response, 'class="customer-notification"')

    def test_existing_customer_does_not_get_flagged_by_new_sale(self):
        customer = Customer.objects.create(name="Client connu")
        payload = self.payload()
        payload["customer"] = customer.pk
        self.assertEqual(self.client.post(reverse("inventory:sale_create"), payload).status_code, 302)
        customer.refresh_from_db()
        self.assertFalse(customer.needs_review)
        self.assertIsNone(customer.review_requested_by)
        self.assertEqual(Sale.objects.get().customer, customer)

    def test_direct_customer_creation_does_not_create_notification(self):
        response = self.client.post(reverse("inventory:customer_create"), {
            "name": "Fiche complète", "reference": "CLI-DIRECT", "credit_limit": "0",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Customer.objects.get(reference="CLI-DIRECT").needs_review)
