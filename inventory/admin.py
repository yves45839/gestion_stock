from django.contrib import admin
from django.db.models import Case, ExpressionWrapper, F, IntegerField, Sum, Value, When
from django.db.models.functions import Coalesce

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


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


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


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "name",
        "brand",
        "category",
        "barcode",
        "minimum_stock",
        "stock_quantity_display",
    )
    search_fields = ("sku", "manufacturer_reference", "name", "barcode")
    list_filter = ("brand", "category")
    inlines = (StockMovementInline,)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        signed_quantity = ExpressionWrapper(
            F("stock_movements__quantity"), output_field=IntegerField()
        )
        exit_quantity = ExpressionWrapper(
            -F("stock_movements__quantity"), output_field=IntegerField()
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
