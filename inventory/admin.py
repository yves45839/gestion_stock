from django.contrib import admin
from django.db.models import Case, ExpressionWrapper, F, IntegerField, Sum, Value, When
from django.db.models.functions import Coalesce

from .models import Brand, Category, MovementType, Product, StockMovement


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


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
        "stock_quantity_display",
    )
    search_fields = ("sku", "manufacturer_reference", "name")
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
