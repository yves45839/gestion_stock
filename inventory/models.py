from django.conf import settings
from django.db import models
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Brand(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Category(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"

    def __str__(self) -> str:
        return self.name


class Product(TimeStampedModel):
    sku = models.CharField("SKU", max_length=100, unique=True)
    manufacturer_reference = models.CharField(
        "Référence fabricant", max_length=100, blank=True
    )
    name = models.CharField("Nom", max_length=255)
    description = models.TextField(blank=True)
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products"
    )
    purchase_price = models.DecimalField(
        "Prix d'achat", max_digits=10, decimal_places=2, blank=True, null=True
    )
    sale_price = models.DecimalField(
        "Prix de vente", max_digits=10, decimal_places=2, blank=True, null=True
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"

    @property
    def stock_quantity(self) -> int:
        entry_total = self.stock_movements.filter(
            movement_type__direction=MovementType.MovementDirection.ENTRY
        ).aggregate(total=Coalesce(Sum("quantity"), Value(0)))
        exit_total = self.stock_movements.filter(
            movement_type__direction=MovementType.MovementDirection.EXIT
        ).aggregate(total=Coalesce(Sum("quantity"), Value(0)))
        return entry_total["total"] - exit_total["total"]


class MovementType(TimeStampedModel):
    class MovementDirection(models.TextChoices):
        ENTRY = "IN", "Entrée"
        EXIT = "OUT", "Sortie"

    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50, unique=True)
    direction = models.CharField(
        max_length=3, choices=MovementDirection.choices, default=MovementDirection.ENTRY
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "type de mouvement"
        verbose_name_plural = "types de mouvements"

    def __str__(self) -> str:
        return self.name


class StockMovementQuerySet(models.QuerySet):
    def with_direction(self):
        return self.select_related("movement_type", "product", "performed_by")


class StockMovement(TimeStampedModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="stock_movements"
    )
    movement_type = models.ForeignKey(
        MovementType, on_delete=models.PROTECT, related_name="stock_movements"
    )
    quantity = models.PositiveIntegerField()
    movement_date = models.DateTimeField(default=timezone.now)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="stock_movements",
    )
    comment = models.TextField(blank=True)
    document_number = models.CharField(max_length=100, blank=True)

    objects = StockMovementQuerySet.as_manager()

    class Meta:
        ordering = ["-movement_date", "-id"]
        verbose_name = "mouvement de stock"
        verbose_name_plural = "mouvements de stock"

    def __str__(self) -> str:
        return f"{self.product} - {self.movement_type} ({self.quantity})"

    @property
    def signed_quantity(self) -> int:
        sign = 1 if self.movement_type.direction == MovementType.MovementDirection.ENTRY else -1
        return sign * self.quantity

    @property
    def direction_label(self) -> str:
        return self.movement_type.get_direction_display()
