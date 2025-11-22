from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("analyses/", views.analytics, name="analytics"),
    path("clients/", views.customers_list, name="customer_list"),
    path("clients/nouveau/", views.customer_create, name="customer_create"),
    path("clients/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("clients/<int:pk>/modifier/", views.customer_update, name="customer_update"),
    path("devis/", views.quotes_list, name="quotes_list"),
    path("devis/nouveau/", views.quote_create, name="quote_create"),
    path("devis/<int:pk>/", views.quote_detail, name="quote_detail"),
    path("devis/<int:pk>/confirmer/", views.quote_confirm, name="quote_confirm"),
    path("mouvements/nouveau/", views.record_movement, name="record_movement"),
    path("produits/<int:pk>/", views.product_detail, name="product_detail"),
    path("versions/<int:version_id>/revert/", views.version_revert, name="version_revert"),
    path("inventaire/", views.inventory_overview, name="inventory_overview"),
    path("ventes/", views.sales_list, name="sales_list"),
    path("ventes/nouvelle/", views.sale_create, name="sale_create"),
    path("ventes/<int:pk>/retour/", views.sale_return, name="sale_return"),
    path("ventes/<int:pk>/ajuster/", views.sale_adjust, name="sale_adjust"),
    path("documents/<int:pk>/<str:doc_type>/", views.sale_document_preview, name="sale_document_preview"),
    path("documents/<int:pk>/<str:doc_type>/pdf/", views.sale_document_pdf, name="sale_document_pdf"),
    path("api/products/scan/", views.lookup_product, name="lookup_product"),
    path("api/sales/scan/", views.scan_sale_product, name="scan_sale_product"),
    path("produits/import/", views.import_products, name="import_products"),
    path("produits/import/modele/", views.export_import_template, name="export_import_template"),
]
