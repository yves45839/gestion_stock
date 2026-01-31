from decimal import Decimal
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.db.models import Case, DecimalField, F, IntegerField, Q, SET_NULL, Sum, Value, When
from django.db.models.fields.files import FieldFile
from django.db.models.functions import Coalesce
from django.forms.models import model_to_dict
from django.urls import reverse
from django.utils import timezone


def generate_customer_reference() -> str:
    return f"CLI-{uuid.uuid4().hex[:8].upper()}"


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Version(TimeStampedModel):
    class Action(models.TextChoices):
        CREATE = "create", "Création"
        UPDATE = "update", "Modification"
        DELETE = "delete", "Suppression"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=255)
    content_object = GenericForeignKey()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="inventory_versions",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    snapshot = models.JSONField(encoder=DjangoJSONEncoder)
    object_url = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def record(cls, instance, action, user=None):
        snapshot = model_to_dict(instance)
        for key, value in snapshot.items():
            if isinstance(value, FieldFile):
                snapshot[key] = value.name
        content_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)
        object_url = ""
        if hasattr(instance, "get_absolute_url"):
            try:
                object_url = instance.get_absolute_url()
            except Exception:
                object_url = ""
        cls.objects.create(
            content_type=content_type,
            object_id=str(instance.pk),
            user=user,
            action=action,
            snapshot=snapshot,
            object_url=object_url,
        )

    @classmethod
    def for_instance(cls, instance):
        content_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)
        return cls.objects.filter(content_type=content_type, object_id=str(instance.pk))

    def restore(self, user=None):
        model_class = self.content_type.model_class()
        if not model_class:
            return None
        pk_field = model_class._meta.pk
        pk_value = pk_field.to_python(self.object_id)
        obj = model_class._default_manager.filter(pk=pk_value).first()
        if obj is None:
            obj = model_class()
            setattr(obj, pk_field.attname, pk_value)
        for field_name, value in self.snapshot.items():
            setattr(obj, field_name, value)
        obj._history_user = user
        obj.save()
        return obj


class VersionedModelMixin(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        Version.record(
            self,
            Version.Action.CREATE if is_new else Version.Action.UPDATE,
            getattr(self, "_history_user", None),
        )

    def delete(self, *args, **kwargs):
        Version.record(self, Version.Action.DELETE, getattr(self, "_history_user", None))
        super().delete(*args, **kwargs)


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


class Site(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "site"
        verbose_name_plural = "sites"

    def __str__(self) -> str:
        return self.name


class SiteAssignment(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="site_assignment",
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.PROTECT,
        related_name="assignments",
    )

    class Meta:
        verbose_name = "site assigné"
        verbose_name_plural = "sites assignés"

    def __str__(self) -> str:
        username = getattr(self.user, "get_username", None)
        user_label = username() if callable(username) else str(self.user)
        return f"{user_label} → {self.site}"


def get_default_site() -> Site | None:
    return Site.objects.order_by("name").first()


class ProductQuerySet(models.QuerySet):
    def with_stock_quantity(self, site=None):
        entry_condition = Q(
            stock_movements__movement_type__direction=MovementType.MovementDirection.ENTRY
        )
        exit_condition = Q(
            stock_movements__movement_type__direction=MovementType.MovementDirection.EXIT
        )
        if site is not None:
            entry_condition &= Q(stock_movements__site=site)
            exit_condition &= Q(stock_movements__site=site)
        signed_quantity = Case(
            When(entry_condition, then=F("stock_movements__quantity")),
            When(exit_condition, then=-F("stock_movements__quantity")),
            default=Value(0),
            output_field=IntegerField(),
        )
        return self.annotate(
            current_stock=Coalesce(
                Sum(signed_quantity),
                Value(0),
                output_field=IntegerField(),
            )
        )

    def for_scan_code(self, scan_value: str):
        normalized = (scan_value or "").strip()
        if not normalized:
            return self.none()
        return self.filter(
            Q(barcode__iexact=normalized)
            | Q(sku__iexact=normalized)
            | Q(manufacturer_reference__iexact=normalized)
        )


class Product(VersionedModelMixin, TimeStampedModel):
    sku = models.CharField("SKU", max_length=100, unique=True)
    manufacturer_reference = models.CharField(
        "Référence fabricant", max_length=100, blank=True
    )
    name = models.CharField("Nom", max_length=255)
    description = models.TextField(blank=True)
    short_description = models.TextField("Description courte", blank=True)
    long_description = models.TextField("Description longue", blank=True)
    tech_specs_json = models.JSONField(
        "Fiche technique (JSON)",
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
    )
    video_links = models.JSONField(
        "Liens vidéo",
        blank=True,
        default=list,
        encoder=DjangoJSONEncoder,
    )
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products"
    )
    barcode = models.CharField("Code-barres", max_length=128, blank=True, null=True, unique=True)
    minimum_stock = models.PositiveIntegerField("Stock minimal", default=0)
    purchase_price = models.DecimalField(
        "Prix d'achat", max_digits=10, decimal_places=2, blank=True, null=True
    )
    sale_price = models.DecimalField(
        "Prix de vente", max_digits=10, decimal_places=2, blank=True, null=True
    )
    image = models.FileField(
        "Image produit",
        upload_to="products/images",
        blank=True,
        null=True,
    )
    image_is_placeholder = models.BooleanField(
        "Image de substitution",
        default=False,
    )
    pending_image = models.FileField(
        "Aperçu image IA",
        upload_to="products/images/pending",
        blank=True,
        null=True,
    )
    pending_image_is_placeholder = models.BooleanField(
        "Aperçu image de substitution",
        default=False,
    )
    is_online = models.BooleanField(
        "Visible en ligne",
        default=True,
    )
    datasheet_url = models.URLField(
        "Fiche technique (URL)",
        blank=True,
        null=True,
    )
    datasheet_pdf = models.FileField(
        "Fiche technique (PDF)",
        upload_to="products/datasheets",
        blank=True,
        null=True,
    )
    datasheet_fetched_at = models.DateTimeField(
        "Fiche technique récupérée",
        blank=True,
        null=True,
    )

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"

    @property
    def stock_quantity(self) -> int:
        annotated_stock = getattr(self, "current_stock", None)
        if annotated_stock is not None:
            return annotated_stock
        entry_total = self.stock_movements.filter(
            movement_type__direction=MovementType.MovementDirection.ENTRY
        ).aggregate(total=Coalesce(Sum("quantity"), Value(0)))
        exit_total = self.stock_movements.filter(
            movement_type__direction=MovementType.MovementDirection.EXIT
        ).aggregate(total=Coalesce(Sum("quantity"), Value(0)))
        return entry_total["total"] - exit_total["total"]

    @property
    def is_below_minimum(self) -> bool:
        if self.minimum_stock <= 0:
            return False
        return self.stock_quantity < self.minimum_stock

    def get_absolute_url(self) -> str:
        return reverse("inventory:product_detail", args=[self.pk])


class ProductAssetJob(TimeStampedModel):
    class Mode(models.TextChoices):
        SINGLE = "single", "Cible unique"
        BATCH = "batch", "Lot"

    class Status(models.TextChoices):
        QUEUED = "queued", "En attente"
        RUNNING = "running", "En cours"
        SUCCESS = "success", "Terminé"
        FAILED = "failed", "Erreur"

    product = models.ForeignKey(
        Product,
        on_delete=SET_NULL,
        null=True,
        blank=True,
        related_name="asset_jobs",
    )
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.BATCH)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    total_products = models.PositiveIntegerField("Total produits", default=0)
    processed_products = models.PositiveIntegerField("Produits traités", default=0)
    assets = models.JSONField("Assets demandés", blank=True, default=list, encoder=DjangoJSONEncoder)
    force_description = models.BooleanField("Forcer description", default=False)
    force_image = models.BooleanField("Forcer image", default=False)
    force_techsheet = models.BooleanField("Forcer fiche technique", default=False)
    force_pdf = models.BooleanField("Forcer brochures PDF", default=False)
    force_videos = models.BooleanField("Forcer vidéos", default=False)
    force_blog = models.BooleanField("Forcer blog", default=False)
    description_changed = models.BooleanField("Description modifiée", default=False)
    image_changed = models.BooleanField("Image modifiée", default=False)
    asset_changes = models.JSONField("Modifications IA", blank=True, default=dict, encoder=DjangoJSONEncoder)
    started_at = models.DateTimeField("Début", null=True, blank=True)
    finished_at = models.DateTimeField("Fin", null=True, blank=True)
    last_message = models.TextField("Dernier message", blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "job d'enrichissement"
        verbose_name_plural = "jobs d'enrichissement"

    @property
    def progress(self) -> int:
        if not self.total_products:
            return 0
        return int((self.processed_products / self.total_products) * 100)


class ProductAssetJobLog(TimeStampedModel):
    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Avertissement"
        ERROR = "error", "Erreur"

    job = models.ForeignKey(
        ProductAssetJob,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    message = models.TextField()

    class Meta:
        ordering = ["created_at"]
        verbose_name = "ligne de journal IA"
        verbose_name_plural = "lignes de journal IA"

    def __str__(self) -> str:
        return f"{self.get_level_display()} - {self.message[:60]}"


class ProductBrochure(TimeStampedModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="brochures",
    )
    title = models.CharField("Titre", max_length=255, blank=True)
    source_url = models.URLField("Source", blank=True)
    file = models.FileField(
        "Brochure (PDF)",
        upload_to="products/brochures",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "brochure PDF"
        verbose_name_plural = "brochures PDF"

    def __str__(self) -> str:
        return self.title or f"Brochure {self.product}"


class ProductAsset(TimeStampedModel):
    class AssetType(models.TextChoices):
        IMAGE = "image", "Image"
        DESCRIPTION = "description", "Description"
        SPECS = "specs", "Fiche technique"
        PDF = "pdf", "Brochure PDF"
        VIDEO = "video", "Vidéo"
        BLOG = "blog", "Blog"

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        APPROVED = "approved", "Validé"
        REJECTED = "rejected", "Refusé"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    asset_type = models.CharField(max_length=20, choices=AssetType.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    source_url = models.URLField(blank=True)
    file = models.FileField(
        upload_to="products/assets",
        blank=True,
        null=True,
    )
    text_content = models.TextField(blank=True)
    metadata = models.JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "asset IA"
        verbose_name_plural = "assets IA"

    def __str__(self) -> str:
        return f"{self.get_asset_type_display()} - {self.product}"


class CustomerQuerySet(models.QuerySet):
    def with_balance(self):
        signed_amount = Case(
            When(
                entries__entry_type="debit",
                then=F("entries__amount"),
            ),
            When(
                entries__entry_type="credit",
                then=-F("entries__amount"),
            ),
            default=Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        return self.annotate(
            account_balance=Coalesce(
                Sum(signed_amount),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)),
            )
        )


class Customer(VersionedModelMixin, TimeStampedModel):
    reference = models.CharField(
        "Référence client",
        max_length=20,
        unique=True,
        default=generate_customer_reference,
    )
    name = models.CharField("Nom du contact", max_length=255)
    company_name = models.CharField("Entreprise", max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    credit_limit = models.DecimalField(
        "Plafond de crédit",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    notes = models.TextField(blank=True)

    objects = CustomerQuerySet.as_manager()

    class Meta:
        ordering = ["name", "company_name"]
        verbose_name = "client"
        verbose_name_plural = "clients"

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        if self.company_name and self.name:
            return f"{self.company_name} - {self.name}"
        return self.company_name or self.name or self.reference

    @property
    def balance(self) -> Decimal:
        annotated = getattr(self, "account_balance", None)
        if annotated is not None:
            return annotated
        signed_amount = Case(
            When(
                entry_type=CustomerAccountEntry.EntryType.DEBIT,
                then=F("amount"),
            ),
            When(
                entry_type=CustomerAccountEntry.EntryType.CREDIT,
                then=-F("amount"),
            ),
            default=Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        aggregate = self.entries.aggregate(
            total=Coalesce(
                Sum(signed_amount),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)),
            )
        )
        return aggregate["total"]

    def get_absolute_url(self) -> str:
        return reverse("inventory:customer_detail", args=[self.pk])


class CustomerAccountEntry(TimeStampedModel):
    class EntryType(models.TextChoices):
        DEBIT = "debit", "Débit"
        CREDIT = "credit", "Crédit"

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    sale = models.ForeignKey(
        "Sale",
        on_delete=models.SET_NULL,
        related_name="account_entries",
        blank=True,
        null=True,
    )
    entry_type = models.CharField(
        max_length=10,
        choices=EntryType.choices,
        default=EntryType.DEBIT,
    )
    label = models.CharField("Libellé", max_length=255)
    occurred_at = models.DateTimeField("Date", default=timezone.now)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "mouvement de compte client"
        verbose_name_plural = "mouvements de comptes client"

    def __str__(self) -> str:
        return f"{self.customer} - {self.label} ({self.entry_type})"

    @property
    def signed_amount(self) -> Decimal:
        sign = 1 if self.entry_type == self.EntryType.DEBIT else -1
        return Decimal(sign) * self.amount


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
        return self.select_related("movement_type", "product", "performed_by", "site")


class StockMovement(VersionedModelMixin, TimeStampedModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="stock_movements"
    )
    movement_type = models.ForeignKey(
        MovementType, on_delete=models.PROTECT, related_name="stock_movements"
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.PROTECT,
        related_name="stock_movements",
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


class InventoryCountSession(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Ouvert"
        CLOSED = "closed", "Cloture"

    name = models.CharField(max_length=200)
    site = models.ForeignKey(
        Site,
        on_delete=models.PROTECT,
        related_name="inventory_sessions",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    started_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_sessions",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "inventaire physique"
        verbose_name_plural = "inventaires physiques"

    def __str__(self) -> str:
        return self.name

    @property
    def is_closed(self) -> bool:
        return self.status == self.Status.CLOSED


class InventoryCountLine(TimeStampedModel):
    session = models.ForeignKey(
        InventoryCountSession,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_lines",
    )
    expected_qty = models.IntegerField(default=0)
    counted_qty = models.IntegerField(default=0)
    difference = models.IntegerField(default=0)
    value_loss = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        unique_together = ("session", "product")
        ordering = ["product__name"]
        verbose_name = "ligne d'inventaire"
        verbose_name_plural = "lignes d'inventaire"

    def __str__(self) -> str:
        return f"{self.session} - {self.product}"

    def recompute(self):
        self.difference = (self.counted_qty or 0) - (self.expected_qty or 0)
        purchase_price = self.product.purchase_price or Decimal("0.00")
        loss_units = abs(self.difference) if self.difference < 0 else 0
        self.value_loss = purchase_price * Decimal(loss_units)


class Sale(VersionedModelMixin, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        CONFIRMED = "confirmed", "Confirmée"

    reference = models.CharField("Référence", max_length=100, unique=True)
    sale_date = models.DateTimeField("Date de vente", default=timezone.now)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        related_name="sales",
        blank=True,
        null=True,
    )
    customer_name = models.CharField("Client", max_length=255, blank=True)
    notes = models.TextField(blank=True)
    site = models.ForeignKey(
        Site,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="sales",
    )
    amount_paid = models.DecimalField(
        "Montant payé",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    class Meta:
        ordering = ["-sale_date", "-id"]
        verbose_name = "vente"
        verbose_name_plural = "ventes"

    def __str__(self) -> str:
        return f"Vente {self.reference}"

    @property
    def customer_display_name(self) -> str:
        if self.customer:
            return self.customer.display_name
        return self.customer_name or "-"

    @property
    def total_amount(self) -> Decimal:
        total = Decimal("0.00")
        for item in self.items.all():
            total += item.total_amount
        return total

    @property
    def returned_quantity(self) -> int:
        if self.status != self.Status.CONFIRMED:
            return 0
        return sum(item.returned_quantity for item in self.items.all())

    @property
    def returned_amount(self) -> Decimal:
        total = Decimal("0.00")
        if self.status != self.Status.CONFIRMED:
            return total
        for item in self.items.all():
            if item.line_type != SaleItem.LineType.PRODUCT:
                continue
            total += item.unit_price * Decimal(item.returned_quantity)
        return total

    def confirm(self, performed_by=None, movement_type=None, site=None):
        if self.status == self.Status.CONFIRMED:
            return
        previous_site = self.site
        with transaction.atomic():
            if movement_type is None:
                movement_type, _ = MovementType.objects.get_or_create(
                    code="VENTE_AUTO",
                    defaults={
                        "name": "Vente (auto)",
                        "direction": MovementType.MovementDirection.EXIT,
                    },
                )
            movement_site = site or get_default_site()
            if movement_site is None:
                raise RuntimeError("Aucun site configuré pour enregistrer la vente.")
            for item in self.items.select_related("product"):
                if (
                    item.line_type != SaleItem.LineType.PRODUCT
                    or not item.product
                    or item.quantity <= 0
                ):
                    continue
                if item.scanned_at is None:
                    item.scanned_at = self.sale_date
                movement = StockMovement.objects.create(
                    product=item.product,
                    movement_type=movement_type,
                    quantity=item.quantity,
                    movement_date=self.sale_date,
                    performed_by=performed_by,
                    document_number=self.reference,
                    comment=f"Vente {self.reference} - {item.product.name}",
                    site=movement_site,
                )
                item.stock_movement = movement
                item.save(update_fields=["stock_movement", "scanned_at"])
                if item.scan_code:
                    SaleScan.objects.update_or_create(
                        sale_item=item,
                        defaults={
                            "raw_code": item.scan_code,
                            "product": item.product,
                            "sale": self,
                            "scanned_by": performed_by,
                            "scanned_at": item.scanned_at or timezone.now(),
                            "notes": f"Scan vente {self.reference}",
                        },
                    )
            self.status = self.Status.CONFIRMED
            site_changed = movement_site != previous_site
            if site_changed:
                self.site = movement_site
            update_fields = ["status"]
            if site_changed:
                update_fields.append("site")
            self.save(update_fields=update_fields)
            self._sync_customer_account_entry()
            self._sync_customer_payment_entry()

    def _sync_customer_account_entry(self):
        if not self.customer:
            return
        amount = self.total_amount
        if amount <= Decimal("0.00"):
            return
        entry = (
            self.account_entries.filter(
                entry_type=CustomerAccountEntry.EntryType.DEBIT,
                sale=self,
            )
            .order_by("-occurred_at", "-id")
            .first()
        )
        label = f"Vente {self.reference}"
        if entry:
            updated_fields: list[str] = []
            if entry.amount != amount:
                entry.amount = amount
                updated_fields.append("amount")
            if entry.label != label:
                entry.label = label
                updated_fields.append("label")
            if entry.occurred_at != self.sale_date:
                entry.occurred_at = self.sale_date
                updated_fields.append("occurred_at")
            if entry.notes != self.notes:
                entry.notes = self.notes
                updated_fields.append("notes")
            if updated_fields:
                entry.save(update_fields=updated_fields)
            return
        CustomerAccountEntry.objects.create(
            customer=self.customer,
            sale=self,
            entry_type=CustomerAccountEntry.EntryType.DEBIT,
            amount=amount,
            occurred_at=self.sale_date,
            label=label,
            notes=self.notes,
        )

    def _sync_customer_payment_entry(self):
        if not self.customer:
            return
        entry = (
            self.account_entries.filter(
                entry_type=CustomerAccountEntry.EntryType.CREDIT,
                sale=self,
            )
            .order_by("-occurred_at", "-id")
            .first()
        )
        if self.amount_paid <= Decimal("0.00"):
            if entry:
                entry.delete()
            return
        label = f"Paiement vente {self.reference}"
        if entry:
            updated_fields: list[str] = []
            if entry.amount != self.amount_paid:
                entry.amount = self.amount_paid
                updated_fields.append("amount")
            if entry.label != label:
                entry.label = label
                updated_fields.append("label")
            if entry.occurred_at != self.sale_date:
                entry.occurred_at = self.sale_date
                updated_fields.append("occurred_at")
            if entry.notes != self.notes:
                entry.notes = self.notes
                updated_fields.append("notes")
            if updated_fields:
                entry.save(update_fields=updated_fields)
            return
        CustomerAccountEntry.objects.create(
            customer=self.customer,
            sale=self,
            entry_type=CustomerAccountEntry.EntryType.CREDIT,
            amount=self.amount_paid,
            occurred_at=self.sale_date,
            label=label,
            notes=self.notes,
        )

    def get_absolute_url(self) -> str:
        if self.status == self.Status.DRAFT:
            return reverse("inventory:quote_detail", args=[self.pk])
        return reverse("inventory:sale_document_preview", args=[self.pk, "invoice"])


class SaleItem(TimeStampedModel):
    class LineType(models.TextChoices):
        PRODUCT = "product", "Produit"
        NOTE = "note", "Note"
        SECTION = "section", "Section"

    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="sale_items",
        blank=True,
        null=True,
    )
    line_type = models.CharField(
        max_length=20,
        choices=LineType.choices,
        default=LineType.PRODUCT,
    )
    description = models.CharField(max_length=255, blank=True)
    position = models.PositiveIntegerField(default=0)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    returned_quantity = models.PositiveIntegerField(default=0)
    scan_code = models.CharField(max_length=255, blank=True)
    scanned_at = models.DateTimeField(blank=True, null=True)
    stock_movement = models.OneToOneField(
        StockMovement,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="sale_item",
    )

    class Meta:
        verbose_name = "ligne de vente"
        verbose_name_plural = "lignes de vente"
        ordering = ["position", "id"]

    def __str__(self) -> str:
        if self.line_type == self.LineType.PRODUCT and self.product:
            return f"{self.product} x {self.quantity}"
        if self.line_type == self.LineType.SECTION:
            return f"Section: {self.description or 'Sans titre'}"
        if self.line_type == self.LineType.NOTE:
            return f"Note: {self.description or 'Sans description'}"
        return "Ligne de vente"

    @property
    def total_amount(self) -> Decimal:
        if self.line_type != self.LineType.PRODUCT:
            return Decimal("0.00")
        net_quantity = max(self.quantity - self.returned_quantity, 0)
        return self.unit_price * Decimal(net_quantity)

    @property
    def available_return_quantity(self) -> int:
        return max(0, self.quantity - self.returned_quantity)

    @property
    def return_amount(self) -> Decimal:
        return self.unit_price * Decimal(self.returned_quantity)

    @staticmethod
    def _get_return_movement_type() -> MovementType:
        movement_type, _ = MovementType.objects.get_or_create(
            code="RETOUR_CLIENT",
            defaults={
                "name": "Retour client",
                "direction": MovementType.MovementDirection.ENTRY,
            },
        )
        return movement_type

    def record_return(self, quantity: int, performed_by=None, movement_type=None, site=None):
        if quantity <= 0:
            return None
        if self.sale.status != Sale.Status.CONFIRMED:
            raise ValueError("Les retours sont uniquement possibles sur des ventes confirmées.")
        available = self.available_return_quantity
        if quantity > available:
            raise ValueError("Impossible de retourner plus d'unités que la quantité vendue disponible.")
        if movement_type is None:
            movement_type = self._get_return_movement_type()
        movement_site = site or self.sale.site or get_default_site()
        if movement_site is None:
            raise RuntimeError("Aucun site configuré pour enregistrer le retour.")
        movement = StockMovement.objects.create(
            product=self.product,
            movement_type=movement_type,
            quantity=quantity,
            movement_date=timezone.now(),
            performed_by=performed_by,
            document_number=self.sale.reference,
            comment=f"Retour {self.sale.reference} - {self.product.name}",
            site=movement_site,
        )
        self.returned_quantity += quantity
        self.save(update_fields=["returned_quantity"])
        return movement


class SaleScan(TimeStampedModel):
    raw_code = models.CharField(max_length=255)
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="sale_scans"
    )
    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name="scans", blank=True, null=True
    )
    sale_item = models.OneToOneField(
        SaleItem,
        on_delete=models.CASCADE,
        related_name="scan_record",
        blank=True,
        null=True,
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="sale_scans",
    )
    scanned_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-scanned_at", "-id"]
        verbose_name = "scan de vente"
        verbose_name_plural = "scans de ventes"

    def __str__(self) -> str:
        return f"Scan {self.raw_code} -> {self.product}"
