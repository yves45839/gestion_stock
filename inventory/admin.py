from django.contrib import admin, messages
from django.db.models import (
    Case,
    Count,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Lower, Replace, Trim

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
    SubCategory,
)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)




@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "category")
    search_fields = ("name", "category__name")
    list_filter = ("category",)

@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)


@admin.register(SiteAssignment)
class SiteAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "site", "created_at", "updated_at")
    search_fields = ("user__username", "user__email", "site__name")
    autocomplete_fields = ("user", "site")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("reference", "display_name", "phone", "email", "balance_display")
    search_fields = ("reference", "name", "company_name", "email", "phone")
    list_filter = ()

    def get_queryset(self, request):
        return super().get_queryset(request).with_balance()

    @admin.display(description="Solde", ordering="account_balance")
    def balance_display(self, obj):
        return f"{obj.balance:.2f} FCFA"


@admin.register(CustomerAccountEntry)
class CustomerAccountEntryAdmin(admin.ModelAdmin):
    list_display = ("customer", "label", "entry_type", "occurred_at", "amount", "sale")
    list_filter = ("entry_type", "occurred_at")
    search_fields = ("customer__name", "customer__company_name", "label", "sale__reference")
    autocomplete_fields = ("customer", "sale")


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    ordering = ("-movement_date",)
    autocomplete_fields = ("movement_type", "performed_by")
    fields = (
        "movement_date",
        "movement_type",
        "quantity",
        "performed_by",
        "document_number",
        "comment",
    )


class DuplicateProductFilter(admin.SimpleListFilter):
    title = "Doublons"
    parameter_name = "duplicate_filter"

    def lookups(self, request, model_admin):
        return (("yes", "Afficher uniquement les doublons"),)

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(duplicate_primary_count__gt=1)
        return queryset


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "name",
        "brand",
        "category",
        "subcategory",
        "is_online",
        "barcode",
        "minimum_stock",
        "stock_quantity_display",
        "duplicate_info",
    )
    search_fields = ("sku", "manufacturer_reference", "name", "barcode")
    list_filter = (DuplicateProductFilter, "brand", "category", "subcategory", "is_online")
    inlines = (StockMovementInline,)
    actions = ("delete_duplicate_products",)

    @staticmethod
    def _normalized_barcode_value(product):
        annotated_value = getattr(product, "normalized_barcode", None)
        if annotated_value is not None:
            return annotated_value.strip()
        return "".join((product.barcode or "").split()).lower()

    @staticmethod
    def _normalized_name_value(product):
        annotated_value = getattr(product, "normalized_name", None)
        if annotated_value is not None:
            return annotated_value.strip()
        return (product.name or "").strip().lower()

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        signed_quantity = ExpressionWrapper(
            F("stock_movements__quantity"), output_field=IntegerField()
        )
        exit_quantity = ExpressionWrapper(
            -F("stock_movements__quantity"), output_field=IntegerField()
        )
        normalized_barcode = Lower(
            Replace(Trim(Coalesce(F("barcode"), Value(""))), Value(" "), Value(""))
        )
        normalized_name = Lower(Trim(Coalesce(F("name"), Value(""))))
        barcode_count = (
            Product.objects.annotate(normalized_barcode=normalized_barcode)
            .exclude(normalized_barcode="")
        )
        qs = qs.annotate(
            normalized_barcode=normalized_barcode,
            normalized_name=normalized_name,
            duplicate_barcode_count=Coalesce(
                Subquery(
                    barcode_count.filter(normalized_barcode=OuterRef("normalized_barcode"))
                    .values("normalized_barcode")
                    .annotate(total=Count("id"))
                    .values("total")[:1],
                ),
                Value(0),
                output_field=IntegerField(),
            ),
            duplicate_name_brand_count=Coalesce(
                Subquery(
                    Product.objects.annotate(
                        normalized_name=Lower(Trim(Coalesce(F("name"), Value(""))))
                    )
                    .filter(normalized_name=OuterRef("normalized_name"), brand=OuterRef("brand"))
                    .values("normalized_name")
                    .annotate(total=Count("id"))
                    .values("total")[:1],
                ),
                Value(0),
                output_field=IntegerField(),
            ),
        )
        barcode_present = ~Q(barcode__isnull=True) & ~Q(barcode="")
        qs = qs.annotate(
            duplicate_primary_count=Case(
                When(barcode_present, then=F("duplicate_barcode_count")),
                default=F("duplicate_name_brand_count"),
                output_field=IntegerField(),
            )
        )
        return qs.annotate(
            current_stock=Coalesce(
                Sum(
                    Case(
                        When(
                            stock_movements__movement_type__direction=
                            MovementType.MovementDirection.ENTRY,
                            then=signed_quantity,
                        ),
                        When(
                            stock_movements__movement_type__direction=
                            MovementType.MovementDirection.EXIT,
                            then=exit_quantity,
                        ),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                Value(0),
            )
        )

    @admin.display(description="Stock actuel", ordering="current_stock")
    def stock_quantity_display(self, obj):
        current_stock = getattr(obj, "current_stock", None)
        return current_stock if current_stock is not None else obj.stock_quantity

    @admin.display(description="Doublon")
    def duplicate_info(self, obj):
        barcode_value = self._normalized_barcode_value(obj)
        duplicate_by_barcode = bool(barcode_value) and obj.duplicate_barcode_count > 1
        duplicate_by_name_brand = not barcode_value and obj.duplicate_name_brand_count > 1
        if duplicate_by_barcode:
            return "Code-barres"
        if duplicate_by_name_brand:
            return "Nom + marque"
        return False

    @admin.action(description="Supprimer les doublons (garder le plus ancien)")
    def delete_duplicate_products(self, request, queryset):
        duplicates = queryset.filter(
            duplicate_primary_count__gt=1
        ).select_related("brand")
        kept = {}
        to_delete = []
        for product in duplicates.order_by(
            "normalized_barcode", "normalized_name", "brand_id", "created_at", "pk"
        ):
            barcode_value = self._normalized_barcode_value(product)
            key = (
                ("barcode", barcode_value)
                if barcode_value
                else (
                    "name_brand",
                    self._normalized_name_value(product),
                    product.brand_id,
                )
            )
            if key not in kept:
                kept[key] = product
            else:
                to_delete.append(product)

        for product in to_delete:
            product.delete()

        self.message_user(
            request,
            f"{len(to_delete)} produit(s) en doublon supprimé(s).",
            level=messages.INFO,
        )


@admin.register(MovementType)
class MovementTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "direction")
    search_fields = ("name", "code")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "movement_type",
        "movement_date",
        "quantity",
        "direction_label",
    )
    list_filter = (
        "movement_type",
        "movement_type__direction",
        "movement_date",
        "product",
        "performed_by",
    )
    search_fields = ("product__sku", "product__name", "document_number", "comment")
    autocomplete_fields = ("product", "movement_type", "performed_by")
    ordering = ("-movement_date",)


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    autocomplete_fields = ("product",)
    fields = ("line_type", "product", "description", "quantity", "unit_price", "scan_code")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("reference", "sale_date", "customer_name", "status", "total_amount_display")
    list_filter = ("status", "sale_date")
    search_fields = ("reference", "customer_name")
    ordering = ("-sale_date",)
    inlines = (SaleItemInline,)

    @admin.display(description="Montant")
    def total_amount_display(self, obj):
        return f"{obj.total_amount:.2f} FCFA"


@admin.register(SaleScan)
class SaleScanAdmin(admin.ModelAdmin):
    list_display = ("raw_code", "product", "sale", "scanned_at", "scanned_by")
    search_fields = ("raw_code", "product__sku", "product__name", "sale__reference")
    list_filter = ("scanned_at",)
