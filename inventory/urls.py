from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", login_required(views.dashboard), name="dashboard"),
    path("analyses/", login_required(views.analytics), name="analytics"),
    path("analyses/ventes-confirmees/pdf/", login_required(views.analytics_sales_pdf), name="analytics_sales_pdf"),
    path("clients/", login_required(views.customers_list), name="customer_list"),
    path("clients/nouveau/", login_required(views.customer_create), name="customer_create"),
    path("clients/<int:pk>/", login_required(views.customer_detail), name="customer_detail"),
    path(
        "clients/<int:pk>/modifier/",
        login_required(views.customer_update),
        name="customer_update",
    ),
    path("devis/", login_required(views.quotes_list), name="quotes_list"),
    path("devis/nouveau/", login_required(views.quote_create), name="quote_create"),
    path("devis/<int:pk>/", login_required(views.quote_detail), name="quote_detail"),
    path("devis/<int:pk>/modifier/", login_required(views.quote_edit), name="quote_edit"),
    path("devis/<int:pk>/confirmer/", login_required(views.quote_confirm), name="quote_confirm"),
    path("mouvements/nouveau/", login_required(views.record_movement), name="record_movement"),
    path("produits/nouveau/", login_required(views.product_create), name="product_create"),
    path("produits/<int:pk>/", login_required(views.product_detail), name="product_detail"),
    path(
        "versions/<int:version_id>/revert/",
        login_required(views.version_revert),
        name="version_revert",
    ),
    path("inventaire/", login_required(views.inventory_overview), name="inventory_overview"),
    path(
        "inventaire/physique/",
        login_required(views.inventory_physical),
        name="inventory_physical",
    ),
    path(
        "inventaire/valorisation/",
        login_required(views.stock_valuation),
        name="stock_valuation",
    ),
    path("ventes/", login_required(views.sales_list), name="sales_list"),
    path("ventes/nouvelle/", login_required(views.sale_create), name="sale_create"),
    path("ventes/<int:pk>/retour/", login_required(views.sale_return), name="sale_return"),
    path("ventes/<int:pk>/ajuster/", login_required(views.sale_adjust), name="sale_adjust"),
    path(
        "documents/<int:pk>/<str:doc_type>/",
        login_required(views.sale_document_preview),
        name="sale_document_preview",
    ),
    path(
        "documents/<int:pk>/<str:doc_type>/pdf/",
        login_required(views.sale_document_pdf),
        name="sale_document_pdf",
    ),
    path("api/products/", views.products_feed, name="products_feed"),
    path("api/products/scan/", login_required(views.lookup_product), name="lookup_product"),
    path("api/sales/scan/", login_required(views.scan_sale_product), name="scan_sale_product"),
    path("ia/", login_required(views.product_asset_bot), name="product_bot"),
    path("produits/import/", login_required(views.import_products), name="import_products"),
    path(
        "produits/import/modele/",
        login_required(views.export_import_template),
        name="export_import_template",
    ),
]
