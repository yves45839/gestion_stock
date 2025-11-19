from django.test import TestCase
from django.utils import timezone

from .models import Brand, Category, MovementType, Product, StockMovement


class StockComputationTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Hikvision")
        self.category = Category.objects.create(name="Caméra")
        self.product = Product.objects.create(
            sku="CAM-001",
            manufacturer_reference="HK-123",
            name="Caméra IP",
            brand=self.brand,
            category=self.category,
        )
        self.reception = MovementType.objects.create(
            name="Réception",
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
            quantity=10,
            movement_date=timezone.now(),
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.sale,
            quantity=3,
            movement_date=timezone.now(),
        )
        StockMovement.objects.create(
            product=self.product,
            movement_type=self.reception,
            quantity=2,
            movement_date=timezone.now(),
        )

        self.assertEqual(self.product.stock_quantity, 9)

    def test_signed_quantity_property(self):
        entry = StockMovement.objects.create(
            product=self.product,
            movement_type=self.reception,
            quantity=5,
            movement_date=timezone.now(),
        )
        exit_move = StockMovement.objects.create(
            product=self.product,
            movement_type=self.sale,
            quantity=4,
            movement_date=timezone.now(),
        )

        self.assertEqual(entry.signed_quantity, 5)
        self.assertEqual(exit_move.signed_quantity, -4)
