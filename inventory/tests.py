from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .bot import ProductAssetBot
from .models import (
    Brand,
    Category,
    Customer,
    CustomerAccountEntry,
    MovementType,
    Product,
    Sale,
    SaleItem,
    SaleScan,
    Site,
    SiteAssignment,
    StockMovement,
)


class StockComputationTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Hikvision")
        self.category = Category.objects.create(name="Camera")
        self.site = Site.objects.create(name="Stock Site")
        self.product = Product.objects.create(
            sku="CAM-001",
            manufacturer_reference="HK-123",
            name="Camera IP",
            barcode="5901234123457",
            brand=self.brand,
            category=self.category,
            minimum_stock=2,
        )
        self.reception = MovementType.objects.create(
            name="Reception",
            code="RECEPTION_TEST",
            direction=MovementType.MovementDirection.ENTRY,
        )
        self.sale = MovementType.objects.create(
            name="Vente",
            code="VENTE_TEST",
            direction=MovementType.MovementDirection.EXIT,
        )

    def test_stock_quantity_updates_with_movements(self):
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.reception,
            site=self.site,
            quantity=10,
            movement_date=timezone.now(),
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.sale,
            site=self.site,
            quantity=3,
            movement_date=timezone.now(),
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.reception,
            site=self.site,
            quantity=2,
            movement_date=timezone.now(),
        )

        self.assertEqual(self.product.stock_quantity, 9)

    def test_signed_quantity_property(self):
        entry = StockMovement.objects.create(
            product=self.product,
            movement_type=self.reception,
            site=self.site,
            quantity=5,
            movement_date=timezone.now(),
        )
        exit_move = StockMovement.objects.create(
            product=self.product,
            movement_type=self.sale,
            site=self.site,
            quantity=4,
            movement_date=timezone.now(),
        )

        self.assertEqual(entry.signed_quantity, 5)
        self.assertEqual(exit_move.signed_quantity, -4)

    def test_below_minimum_indicator(self):
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.reception,
            site=self.site,
            quantity=1,
            movement_date=timezone.now(),
        )
        self.assertTrue(self.product.is_below_minimum)

    def test_for_scan_code_matches_barcode(self):
        matched = Product.objects.for_scan_code("5901234123457").first()
        self.assertIsNotNone(matched)
        self.assertEqual(matched, self.product)

    def test_for_scan_code_matches_manufacturer_reference(self):
        matched = Product.objects.for_scan_code(self.product.manufacturer_reference).first()
        self.assertEqual(matched, self.product)


class InventoryViewTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Ubiquiti")
        self.category = Category.objects.create(name="Antenne")
        self.product = Product.objects.create(
            sku="ANT-001",
            name="Antenne extérieure",
            manufacturer_reference="ANT-001",
            barcode="321321321000",
            brand=self.brand,
            category=self.category,
            minimum_stock=5,
        )
        self.site = Site.objects.create(name="Inventory Site")
        self.entry_type, _ = MovementType.objects.get_or_create(
            code="RECEPTION_VIEW",
            defaults={
                "name": "Réception",
                "direction": MovementType.MovementDirection.ENTRY,
            },
        )
        self.exit_type, _ = MovementType.objects.get_or_create(
            code="VENTE_VIEW",
            defaults={
                "name": "Vente",
                "direction": MovementType.MovementDirection.EXIT,
            },
        )
        self.adjust_plus, _ = MovementType.objects.get_or_create(
            code="AJUSTEMENT_PLUS",
            defaults={
                "name": "Ajustement +",
                "direction": MovementType.MovementDirection.ENTRY,
            },
        )
        self.adjust_minus, _ = MovementType.objects.get_or_create(
            code="AJUSTEMENT_MOINS",
            defaults={
                "name": "Ajustement -",
                "direction": MovementType.MovementDirection.EXIT,
            },
        )
        self.user = get_user_model().objects.create_user(
            username="gestionnaire",
            password="test-secret",
            email="gestion@example.com",
        )
        SiteAssignment.objects.create(user=self.user, site=self.site)
        self.client.force_login(self.user)

    def test_dashboard_renders(self):
        response = self.client.get(reverse("inventory:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("total_products", response.context)

    def test_record_movement_view_creates_entry(self):
        self.client.force_login(self.user)
        payload = {
            "product": self.product.pk,
            "movement_type": self.entry_type.pk,
            "quantity": 7,
            "movement_date": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "document_number": "REC-001",
            "comment": "Test réception",
            "site": self.site.pk,
        }
        response = self.client.post(reverse("inventory:record_movement"), data=payload)
        self.assertEqual(response.status_code, 302)
        movement = StockMovement.objects.get()
        self.assertEqual(movement.performed_by, self.user)
        self.assertEqual(movement.quantity, 7)

    def test_inventory_adjustment_creates_movement(self):
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.entry_type,
            site=self.site,
            quantity=10,
            movement_date=timezone.now(),
        )
        payload = {
            "product": self.product.pk,
            "counted_quantity": 8,
            "comment": "Inventaire",
            "site": self.site.pk,
        }
        response = self.client.post(reverse("inventory:inventory_overview"), data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StockMovement.objects.count(), 2)
        adjustment = StockMovement.objects.order_by("-id").first()
        self.assertEqual(adjustment.movement_type, self.adjust_minus)
        self.assertEqual(adjustment.quantity, 2)

    def test_lookup_product_endpoint(self):
        response = self.client.get(reverse("inventory:lookup_product"), {"code": self.product.barcode})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["product"]["id"], self.product.id)

    def test_lookup_product_endpoint_returns_not_found_for_missing_product(self):
        response = self.client.get(reverse("inventory:lookup_product"), {"code": "000000"})
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertFalse(data["found"])
        self.assertFalse(data["created"])
        self.assertFalse(Product.objects.filter(barcode="000000").exists())

    def test_inventory_overview_scan_filter(self):
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.entry_type,
            site=self.site,
            quantity=5,
            movement_date=timezone.now(),
        )
        response = self.client.get(reverse("inventory:inventory_overview"), {"scan": self.product.sku})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["products"]), 1)

    def test_inventory_overview_scan_does_not_create_product_when_missing(self):
        response = self.client.get(reverse("inventory:inventory_overview"), {"scan": "NEWCODE123"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(barcode="NEWCODE123").exists())
        self.assertIn("Produit introuvable", response.context["scan_message"])


class ImportViewTests(TestCase):
    def setUp(self):
        self.entry_type = MovementType.objects.create(
            name="Réception",
            code="RECEPTION_IMPORT",
            direction=MovementType.MovementDirection.ENTRY,
        )
        self.user = get_user_model().objects.create_user(
            username="importer",
            password="pass-import",
            email="import@example.com",
        )
        self.client.force_login(self.user)

    def test_import_creates_products_and_stock(self):
        csv_content = (
            "SKU,Ref,Désignation,Description,Marque,Catégorie,Code-barres,Stock minimal,Prix achat,Prix vente,Qté\n"
            "CAM-NEW-01,REF-100,Produit test,Camera PoE,Dahua,Caméra,1234567890123,4,120.5,199.9,5\n"
        )
        upload = SimpleUploadedFile("stock.csv", csv_content.encode("utf-8"), content_type="text/csv")
        response = self.client.post(
            reverse("inventory:import_products"),
            {
                "encoding": "utf-8",
                "apply_quantity": "on",
                "movement_type": self.entry_type.pk,
                "file": upload,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(sku="CAM-NEW-01")
        self.assertEqual(product.manufacturer_reference, "REF-100")
        self.assertEqual(product.description, "Camera PoE")
        self.assertEqual(product.brand.name, "Dahua")
        self.assertEqual(product.category.name, "Caméra")
        self.assertEqual(product.barcode, "1234567890123")
        self.assertEqual(product.minimum_stock, 4)
        self.assertEqual(float(product.purchase_price), 120.5)
        self.assertEqual(float(product.sale_price), 199.9)
        self.assertEqual(StockMovement.objects.filter(product=product).count(), 1)

    def test_import_handles_missing_quantity(self):
        csv_content = "Ref;Désignation\nREF-200;Produit sans qty\n"
        upload = SimpleUploadedFile("stock.csv", csv_content.encode("latin-1"), content_type="text/csv")
        response = self.client.post(
            reverse("inventory:import_products"),
            {
                "encoding": "latin-1",
                "apply_quantity": "",
                "file": upload,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product.objects.filter(sku="REF-200").exists())
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_import_updates_existing_product_fields(self):
        product = Product.objects.create(
            sku="UPD-001",
            name="Ancien nom",
            brand=Brand.objects.create(name="OldBrand"),
            category=Category.objects.create(name="OldCategory"),
        )
        csv_content = (
            "SKU,Désignation,Marque,Catégorie,Stock minimal,Prix achat,Prix vente\n"
            "UPD-001,Nouveau nom,Nouvelle Marque,Nouvelle Catégorie,7,10.5,20.5\n"
        )
        upload = SimpleUploadedFile("stock.csv", csv_content.encode("utf-8"), content_type="text/csv")
        response = self.client.post(
            reverse("inventory:import_products"),
            {
                "encoding": "utf-8",
                "apply_quantity": "",
                "file": upload,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.name, "Nouveau nom")
        self.assertEqual(product.brand.name, "Nouvelle Marque")
        self.assertEqual(product.category.name, "Nouvelle Catégorie")
        self.assertEqual(product.minimum_stock, 7)
        self.assertEqual(float(product.purchase_price), 10.5)
        self.assertEqual(float(product.sale_price), 20.5)

    def test_export_template_returns_expected_csv(self):
        response = self.client.get(reverse("inventory:export_import_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        content = response.content.decode("utf-8")
        self.assertIn("SKU;Ref;Désignation;Description;Marque;Catégorie;Code-barres;Stock minimal;Prix achat;Prix vente;Qté;Unité", content.splitlines()[0])



class SalesWorkflowTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="SalesBrand")
        self.category = Category.objects.create(name="Switch")
        self.product = Product.objects.create(
            sku="SW-001",
            name="Switch manageable",
            barcode="QR-SW-001",
            brand=self.brand,
            category=self.category,
            sale_price=Decimal("120.00"),
        )
        self.site = Site.objects.create(name="Sales Site")
        self.entry_type = MovementType.objects.create(
            name="Reception",
            code="SALE_RECEPTION",
            direction=MovementType.MovementDirection.ENTRY,
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.entry_type,
            site=self.site,
            quantity=20,
            movement_date=timezone.now(),
        )
        self.user = get_user_model().objects.create_user(
            username="salesman",
            password="strong-pass",
            email="sales@example.com",
        )
        self.client.force_login(self.user)

    def test_sale_confirmation_creates_exit_movements(self):
        sale = Sale.objects.create(
            reference="VENTE-100",
            sale_date=timezone.now(),
            customer_name="ACME",
        )
        item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=5,
            unit_price=Decimal("150.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            line_type=SaleItem.LineType.NOTE,
            description="Vente speciale",
        )
        sale.confirm(site=self.site)
        item.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CONFIRMED)
        self.assertIsNotNone(item.stock_movement)
        self.assertEqual(
            item.stock_movement.movement_type.direction,
            MovementType.MovementDirection.EXIT,
        )
        self.assertEqual(self.product.stock_quantity, 15)

    def test_sale_create_view_records_sale_and_stock(self):
        user = self.user
        self.client.force_login(user)
        sale_date = timezone.now()
        payload = {
            "reference": "VENTE-200",
            "sale_date": sale_date.strftime("%Y-%m-%dT%H:%M"),
            "customer_name": "Client Test",
            "amount_paid": "200",
            "notes": "Livraison express",
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-line_type": "product",
            "items-0-product": "",
            "items-0-quantity": "3",
            "items-0-unit_price": "",
            "items-0-scan_code": self.product.barcode,
            "items-0-description": "",
            "items-0-DELETE": "",
            "items-1-line_type": "note",
            "items-1-product": "",
            "items-1-quantity": "0",
            "items-1-unit_price": "0",
            "items-1-scan_code": "",
            "items-1-description": "Note de service",
            "items-1-DELETE": "",
        }
        response = self.client.post(reverse("inventory:sale_create"), data=payload)
        error_info = None
        if response.context:
            formset = response.context.get("formset")
            if formset is not None:
                error_info = formset.errors
        self.assertEqual(response.status_code, 302, error_info)
        sale = Sale.objects.get(reference="VENTE-200")
        self.assertIsNotNone(sale.customer)
        self.assertEqual(sale.customer.name, "Client Test")
        self.assertEqual(sale.amount_paid, Decimal("200"))
        self.assertEqual(sale.items.count(), 2)
        sale_item = sale.items.filter(line_type=SaleItem.LineType.PRODUCT).first()
        sale_item.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CONFIRMED)
        self.assertIsNotNone(sale_item.stock_movement)
        self.assertEqual(sale_item.scan_code, self.product.barcode)
        self.assertIsNotNone(sale_item.scanned_at)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 17)
        self.assertEqual(
            StockMovement.objects.filter(
                product=self.product,
                movement_type__direction=MovementType.MovementDirection.EXIT,
            ).count(),
            1,
        )
        self.assertTrue(
            SaleScan.objects.filter(sale=sale, raw_code=self.product.barcode).exists()
        )
        self.assertTrue(sale.items.filter(line_type=SaleItem.LineType.NOTE).exists())

    def test_quote_create_and_confirm_flow(self):
        sale_date = timezone.now()
        payload = {
            "reference": "DEVIS-001",
            "sale_date": sale_date.strftime("%Y-%m-%dT%H:%M"),
            "customer_name": "Prospect",
            "amount_paid": "0",
            "notes": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-line_type": "product",
            "items-0-product": "",
            "items-0-quantity": "2",
            "items-0-unit_price": "",
            "items-0-scan_code": self.product.barcode,
            "items-0-description": "",
            "items-0-DELETE": "",
        }
        response = self.client.post(reverse("inventory:quote_create"), data=payload)
        self.assertEqual(response.status_code, 302)
        quote = Sale.objects.get(reference="DEVIS-001")
        self.assertEqual(quote.status, Sale.Status.DRAFT)
        self.assertEqual(quote.items.count(), 1)
        confirm_response = self.client.post(reverse("inventory:quote_confirm", args=[quote.pk]))
        self.assertEqual(confirm_response.status_code, 302)
        quote.refresh_from_db()
        self.assertEqual(quote.status, Sale.Status.CONFIRMED)
        self.assertEqual(quote.items.first().stock_movement.quantity, 2)

    def test_document_preview_invoice(self):
        sale = Sale.objects.create(
            reference="VENTE-300",
            sale_date=timezone.now(),
            customer_name="Client Doc",
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=1,
            unit_price=Decimal("120.00"),
        )
        sale.confirm(site=self.site)
        response = self.client.get(reverse("inventory:sale_document_preview", args=[sale.pk, "invoice"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("FACTURE", response.content.decode())

    def test_sale_create_rejects_payment_higher_than_total(self):
        user = get_user_model().objects.create_user(
            username="salesman2",
            password="strong-pass",
            email="sales2@example.com",
        )
        self.client.force_login(user)
        sale_date = timezone.now()
        payload = {
            "reference": "VENTE-OVER",
            "sale_date": sale_date.strftime("%Y-%m-%dT%H:%M"),
            "customer_name": "Client Test",
            "amount_paid": "9999",
            "notes": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-line_type": "product",
            "items-0-product": "",
            "items-0-quantity": "1",
            "items-0-unit_price": "",
            "items-0-scan_code": self.product.barcode,
            "items-0-description": "",
            "items-0-DELETE": "",
        }
        response = self.client.post(reverse("inventory:sale_create"), data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("amount_paid", response.context["sale_form"].errors)
        self.assertFalse(Sale.objects.filter(reference="VENTE-OVER").exists())

    def test_sales_list_context_shows_totals(self):
        sale = Sale.objects.create(
            reference="VENTE-CTX",
            sale_date=timezone.now(),
            customer_name="Client X",
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=2,
            unit_price=Decimal("150.00"),
            scan_code="CTX-CODE",
        )
        SaleItem.objects.create(
            sale=sale,
            line_type=SaleItem.LineType.SECTION,
            description="Materiel",
        )
        sale.confirm(site=self.site)
        response = self.client.get(reverse("inventory:sales_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sales"], 1)
        self.assertEqual(response.context["total_quantity"], 2)
        self.assertContains(response, "VENTE-CTX")
        self.assertEqual(response.context["sales"][0].scan_total, 1)

    def test_scan_sale_product_endpoint(self):
        response = self.client.get(
            reverse("inventory:scan_sale_product"),
            {"code": self.product.barcode},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["product"]["id"], self.product.id)
        self.assertTrue(
            SaleScan.objects.filter(raw_code=self.product.barcode).exists()
        )


class CustomerAccountTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            reference="CLI-001",
            name="Jean Client",
            company_name="ACME",
            email="client@example.com",
            phone="0600000000",
        )
        self.brand = Brand.objects.create(name="Tplink")
        self.category = Category.objects.create(name="Routeur")
        self.product = Product.objects.create(
            sku="RT-01",
            name="Routeur",
            brand=self.brand,
            category=self.category,
        )
        self.site = Site.objects.create(name="Accounts Site")

    def test_balance_updates_with_entries(self):
        CustomerAccountEntry.objects.create(
            customer=self.customer,
            entry_type=CustomerAccountEntry.EntryType.DEBIT,
            label="Facture",
            amount=Decimal("150.00"),
        )
        CustomerAccountEntry.objects.create(
            customer=self.customer,
            entry_type=CustomerAccountEntry.EntryType.CREDIT,
            label="Paiement",
            amount=Decimal("50.00"),
        )
        refreshed = Customer.objects.with_balance().get(pk=self.customer.pk)
        self.assertEqual(refreshed.balance, Decimal("100.00"))

    def test_sale_confirmation_creates_customer_entry(self):
        sale = Sale.objects.create(
            reference="VTE-CLI",
            sale_date=timezone.now(),
            customer=self.customer,
            customer_name="Jean Client",
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=2,
            unit_price=Decimal("30.00"),
        )
        sale.confirm(site=self.site)
        debit_entry = sale.account_entries.get(entry_type=CustomerAccountEntry.EntryType.DEBIT)
        self.assertEqual(debit_entry.amount, Decimal("60.00"))
        self.assertEqual(debit_entry.customer, self.customer)
        self.assertFalse(
            sale.account_entries.filter(entry_type=CustomerAccountEntry.EntryType.CREDIT).exists()
        )

    def test_sale_confirmation_records_payment_entry(self):
        sale = Sale.objects.create(
            reference="VTE-CLI-PAY",
            sale_date=timezone.now(),
            customer=self.customer,
            customer_name="Jean Client",
            amount_paid=Decimal("40.00"),
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=2,
            unit_price=Decimal("30.00"),
        )
        sale.confirm(site=self.site)
        credit_entry = sale.account_entries.get(entry_type=CustomerAccountEntry.EntryType.CREDIT)
        self.assertEqual(credit_entry.amount, Decimal("40.00"))
        debit_entry = sale.account_entries.get(entry_type=CustomerAccountEntry.EntryType.DEBIT)
        self.assertEqual(debit_entry.amount, Decimal("60.00"))
        refreshed = Customer.objects.with_balance().get(pk=self.customer.pk)
        self.assertEqual(refreshed.balance, Decimal("20.00"))


class CustomerViewTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            reference="CLI-990",
            name="Client Vue",
            company_name="VueCorp",
            email="vue@example.com",
        )
        self.user = get_user_model().objects.create_user(
            username="customer-user",
            password="password123",
            email="customer@example.com",
        )
        self.client.force_login(self.user)

    def test_customer_list_view(self):
        response = self.client.get(reverse("inventory:customer_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Client Vue")

    def test_add_entry_from_detail_view(self):
        url = reverse("inventory:customer_detail", args=[self.customer.pk])
        payload = {
            "entry_type": CustomerAccountEntry.EntryType.CREDIT,
            "label": "Règlement",
            "amount": "50",
            "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            "notes": "Paiement carte",
        }
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            CustomerAccountEntry.objects.filter(customer=self.customer).count(),
            1,
        )


class ProductBotViewTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="TP-Link")
        self.category = Category.objects.create(name="Switch")
        self.product = Product.objects.create(
            sku="IA-001",
            name="Switch manageable PoE",
            description="Switch rackable avec fonctionnalités de base.",
            brand=self.brand,
            category=self.category,
        )
        self.user = get_user_model().objects.create_user(
            username="ia-user",
            password="password123",
            email="ia@example.com",
            is_staff=True,
        )
        self.site = Site.objects.create(name="IA Site")
        SiteAssignment.objects.create(user=self.user, site=self.site)
        self.client.force_login(self.user)

    def test_product_bot_view_exposes_quality_score_on_catalog_rows(self):
        response = self.client.get(reverse("inventory:product_bot"))

        self.assertEqual(response.status_code, 200)
        products = list(response.context["catalog_products"])
        self.assertEqual(len(products), 1)
        self.assertTrue(hasattr(products[0], "quality_report"))
        self.assertGreaterEqual(products[0].quality_report.score, 0)
        self.assertContains(response, "Score IA")


class ProductQualityAgentTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Mikrotik")
        self.category = Category.objects.create(name="Routeur")

    def test_evaluate_returns_low_score_for_sparse_product(self):
        from .quality_agent import ProductQualityAgent

        product = Product.objects.create(
            sku="Q-LOW-1",
            name="Routeur",
            brand=self.brand,
            category=self.category,
        )

        report = ProductQualityAgent(threshold=70, bot=object()).evaluate(product)

        self.assertLess(report.score, 70)
        self.assertIn("Description principale absente.", report.issues)

    def test_improve_if_needed_updates_product_when_bot_returns_changes(self):
        from .quality_agent import ProductQualityAgent

        class FakeBot:
            def ensure_assets(self, product, **kwargs):
                product.short_description = "Performance élevée et installation rapide."
                product.long_description = "x" * 500
                product.description = "x" * 500
                product.tech_specs_json = {"ports": "8", "poe": "oui", "uplink": "2", "débit": "1Gbps"}
                product.video_links = ["https://example.com/video"]
                return {
                    "short_description_changed": True,
                    "long_description_changed": True,
                    "description_changed": True,
                    "tech_specs_changed": True,
                    "videos_changed": True,
                }

        product = Product.objects.create(
            sku="Q-LOW-2",
            name="Switch manageable",
            brand=self.brand,
            category=self.category,
        )

        result = ProductQualityAgent(threshold=80, bot=FakeBot()).improve_if_needed(product)
        product.refresh_from_db()

        self.assertTrue(result["changed"])
        self.assertIn("score_after", result)
        self.assertGreater(result["score_after"], result["score"])
        self.assertTrue(product.short_description)
        self.assertTrue(product.long_description)

    def test_evaluate_detects_placeholder_flag_as_fake_image(self):
        from .quality_agent import ProductQualityAgent

        product = Product.objects.create(
            sku="Q-IMG-1",
            name="Caméra dôme",
            brand=self.brand,
            category=self.category,
            image=SimpleUploadedFile("camera.png", b"fake", content_type="image/png"),
            image_is_placeholder=True,
        )

        report = ProductQualityAgent(threshold=70, bot=object()).evaluate(product)

        self.assertEqual(report.details["image"], 1)
        self.assertTrue(any("Image non exploitable détectée" in issue for issue in report.issues))

    def test_evaluate_detects_low_quality_image_as_fake(self):
        from io import BytesIO

        from .quality_agent import ProductQualityAgent

        image = Image.new("RGB", (100, 100), color=(180, 180, 180))
        payload = BytesIO()
        image.save(payload, format="PNG")

        product = Product.objects.create(
            sku="Q-IMG-2",
            name="Caméra tourelle",
            brand=self.brand,
            category=self.category,
            image=SimpleUploadedFile("tiny_uniform.png", payload.getvalue(), content_type="image/png"),
            image_is_placeholder=False,
        )

        report = ProductQualityAgent(threshold=70, bot=object()).evaluate(product)

        self.assertEqual(report.details["image"], 1)
        self.assertTrue(any("Image non exploitable détectée" in issue for issue in report.issues))


    def test_evaluate_marks_mid_quality_image_as_suspect(self):
        from io import BytesIO

        from .quality_agent import ProductQualityAgent

        image = Image.new("RGB", (350, 350))
        palette = [int(i * (255 / 19)) for i in range(20)]
        for x in range(350):
            shade = palette[x % len(palette)]
            for y in range(350):
                image.putpixel((x, y), (shade, shade, shade))
        payload = BytesIO()
        image.save(payload, format="PNG")

        product = Product.objects.create(
            sku="Q-IMG-3",
            name="Caméra intermédiaire",
            brand=self.brand,
            category=self.category,
            image=SimpleUploadedFile("mid_quality.png", payload.getvalue(), content_type="image/png"),
            image_is_placeholder=False,
        )

        report = ProductQualityAgent(threshold=70, bot=object()).evaluate(product)

        self.assertEqual(report.details["image"], 6)
        self.assertTrue(any("potentiellement peu exploitable" in issue for issue in report.issues))


class ProductImageSearchPriorityTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="SerperBrand")
        self.category = Category.objects.create(name="NVR")
        self.product = Product.objects.create(
            sku="SP-001",
            manufacturer_reference="SP-REF-001",
            name="NVR 8 canaux",
            brand=self.brand,
            category=self.category,
        )


    @override_settings(
        PRODUCT_BOT_LOCAL_IMAGE_SEARCH_ENABLED=False,
        PRODUCT_BOT_GENERATE_FALLBACK_IMAGE=False,
    )
    def test_local_image_lookup_is_disabled_by_default(self):
        bot = ProductAssetBot()
        bot._find_local_image = MagicMock(return_value=Path("/tmp/local-image.jpg"))
        bot._find_search_image = MagicMock(return_value=(None, None))

        changed = bot.ensure_image(self.product, image_field="pending_image")

        self.assertFalse(changed)
        bot._find_local_image.assert_not_called()
        bot._find_search_image.assert_called_once_with(self.product)

    def test_serper_is_used_before_google(self):
        bot = ProductAssetBot()
        bot.serper_search = MagicMock()
        bot.google_search = MagicMock()
        bot.serper_search.search_image.return_value = "https://serper.dev/image.jpg"
        bot.serper_search.last_status = "ok"

        image_url, source = bot._find_search_image(self.product)

        self.assertEqual(source, "serper")
        self.assertEqual(image_url, "https://serper.dev/image.jpg")
        bot.serper_search.search_image.assert_called_once()
        bot.google_search.search_image.assert_not_called()

    @override_settings(PRODUCT_BOT_SERPER_IMAGE_MAX_CREDITS=4)
    def test_no_result_stops_after_four_credits(self):
        bot = ProductAssetBot()
        bot.serper_search = MagicMock()
        bot.google_search = MagicMock()
        bot.serper_search.search_image.return_value = None
        bot.serper_search.last_status = "no_results"

        image_url, source = bot._find_search_image(self.product)

        self.assertIsNone(source)
        self.assertIsNone(image_url)
        self.assertEqual(bot.serper_search.search_image.call_count, 4)
        bot.google_search.search_image.assert_not_called()

    @override_settings(
        PRODUCT_BOT_LOCAL_IMAGE_SEARCH_ENABLED=False,
        PRODUCT_BOT_IMAGE_URL_TEMPLATE="https://cdn.example.com/{reference}.jpg",
    )
    def test_template_url_is_not_used_when_serper_has_no_result(self):
        bot = ProductAssetBot()
        bot.serper_search = MagicMock()
        bot.serper_search.search_image.return_value = None
        bot.serper_search.last_status = "no_results"

        changed = bot.ensure_image(self.product, image_field="pending_image")

        self.assertFalse(changed)

    @override_settings(
        PRODUCT_BOT_LOCAL_IMAGE_SEARCH_ENABLED=False,
        PRODUCT_BOT_IMAGE_URL_TEMPLATE="",
        PRODUCT_BOT_GENERATE_FALLBACK_IMAGE=True,
    )
    def test_no_generated_preview_when_no_image_source(self):
        bot = ProductAssetBot()
        bot.serper_search = None
        bot.serper_search_status = "disabled"

        changed = bot.ensure_image(self.product, image_field="pending_image")

        self.assertFalse(changed)
        self.assertFalse(bool(self.product.pending_image))

    @override_settings(
        PRODUCT_BOT_LOCAL_IMAGE_SEARCH_ENABLED=False,
        PRODUCT_BOT_ALLOW_PLACEHOLDERS=False,
    )
    def test_second_serper_candidate_is_used_when_first_is_placeholder(self):
        bot = ProductAssetBot()
        bot.serper_search = MagicMock()
        bot.serper_search.search_image.return_value = "https://placehold.co/1200x1200.png"
        bot.serper_search.last_status = "ok"
        bot.serper_search.last_candidates = [
            "https://placehold.co/1200x1200.png",
            "https://cdn.example.com/image-real.jpg",
        ]

        image_url, source = bot._find_search_image(self.product)

        self.assertEqual(source, "serper")
        self.assertEqual(image_url, "https://cdn.example.com/image-real.jpg")


class GoogleImageSearchClientTests(TestCase):
    @override_settings(
        PRODUCT_BOT_GOOGLE_IMAGE_SEARCH_ENABLED=True,
        GOOGLE_CUSTOM_SEARCH_API_KEY="dummy-key",
        GOOGLE_CUSTOM_SEARCH_ENGINE_ID="dummy-engine",
        PRODUCT_BOT_GOOGLE_IMAGE_DAILY_LIMIT=10,
        PRODUCT_BOT_GOOGLE_IMAGE_NUM_MAX=10,
    )
    def test_google_num_is_capped_at_four(self):
        bot = ProductAssetBot()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"items": [{"link": "https://img.local/google.jpg"}]}
        bot.google_search.session.get = MagicMock(return_value=mock_response)

        result = bot.google_search.search_image("cam test")

        self.assertEqual(result, "https://img.local/google.jpg")
        _, kwargs = bot.google_search.session.get.call_args
        self.assertEqual(kwargs["params"]["num"], 4)

class SerperImageSearchClientTests(TestCase):
    @override_settings(
        PRODUCT_BOT_SERPER_IMAGE_SEARCH_ENABLED=True,
        SERPER_API_KEY="dummy-key",
        PRODUCT_BOT_SERPER_IMAGE_DAILY_LIMIT=10,
        PRODUCT_BOT_SERPER_IMAGE_NUM_MAX=10,
    )
    def test_serper_num_is_capped_at_four(self):
        bot = ProductAssetBot()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"images": [{"imageUrl": "https://img.local/a.jpg"}]}
        bot.serper_search.session.post = MagicMock(return_value=mock_response)

        result = bot.serper_search.search_image("cam test")

        self.assertEqual(result, "https://img.local/a.jpg")
        _, kwargs = bot.serper_search.session.post.call_args
        self.assertEqual(kwargs["json"]["num"], 4)



class ProductImageQualityTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Reolink")
        self.category = Category.objects.create(name="Caméra")
        self.product = Product.objects.create(
            sku="IMG-001",
            manufacturer_reference="RLK-100",
            name="Caméra extérieure RLK",
            brand=self.brand,
            category=self.category,
        )
        self.bot = ProductAssetBot()
        self.bot.enable_ocr = False
        self.bot.min_image_bytes = 100

    @staticmethod
    def _build_image_bytes(size=(800, 600), color=(120, 120, 120)) -> bytes:
        image = Image.new("RGB", size, color=color)
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_rejects_too_small_images(self):
        payload = self._build_image_bytes(size=(120, 120))
        report = self.bot._evaluate_downloaded_image(self.product, payload)

        self.assertFalse(report["valid"])
        self.assertIn("resolution insuffisante", report["reason"])

    def test_rejects_uniform_images(self):
        payload = self._build_image_bytes(size=(900, 900), color=(128, 128, 128))
        report = self.bot._evaluate_downloaded_image(self.product, payload)

        self.assertFalse(report["valid"])
        self.assertIn("uniforme", report["reason"])

    def test_accepts_detailed_images(self):
        image = Image.effect_noise((900, 900), 90).convert("RGB")
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        report = self.bot._evaluate_downloaded_image(self.product, buffer.getvalue())

        self.assertTrue(report["valid"])
        self.assertEqual(report["reason"], "ok")


class ProductDescriptionPromptStyleTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Ubiquiti")
        self.category = Category.objects.create(name="Réseau")
        self.product = Product.objects.create(
            sku="DESC-001",
            manufacturer_reference="UBT-001",
            name="Routeur Wi-Fi Pro",
            brand=self.brand,
            category=self.category,
        )
        self.bot = ProductAssetBot()

    def test_short_description_prompt_mentions_premium_ecommerce_style(self):
        prompt = self.bot._build_short_description_prompt(self.product)

        self.assertIn("style e-commerce premium", prompt)
        self.assertIn("3 bullets maximum", prompt)
        self.assertIn("Ne cite pas de concurrents", prompt)

    def test_long_description_prompt_enforces_structured_sections(self):
        prompt = self.bot._build_long_description_prompt(self.product)

        self.assertIn("Une accroche", prompt)
        self.assertIn("Presentation", prompt)
        self.assertIn("Usages recommandes", prompt)
        self.assertIn("Points forts", prompt)
        self.assertIn("Caracteristiques techniques detaillees", prompt)
        self.assertIn("Contenu du pack", prompt)
        self.assertIn("mini FAQ", prompt)
