import csv
import io
from datetime import datetime, time, timedelta

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.forms import formset_factory
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.template.loader import render_to_string

from .forms import (
    CSVImportForm,
    CustomerAccountEntryForm,
    CustomerForm,
    InventoryAdjustmentForm,
    MovementHeaderForm,
    MovementLineForm,
    StockMovementForm,
    ProductForm,
    SaleForm,
    SaleItemFormSet,
    SaleAdjustmentItemForm,
    SaleReturnItemForm,
)
from .models import (
    Brand,
    Category,
    Customer,
    CustomerAccountEntry,
    InventoryCountLine,
    InventoryCountSession,
    MovementType,
    Product,
    Sale,
    SaleItem,
    SaleScan,
    Site,
    StockMovement,
    Version,
    get_default_site,
)

try:
    from weasyprint import HTML
except ImportError:  # pragma: no cover
    HTML = None

PERIOD_CHOICES = [
    ("today", "Aujourd'hui"),
    ("yesterday", "Hier"),
    ("day", "Dernier jour"),
    ("week", "Dernière semaine"),
    ("month", "Ce mois"),
    ("3months", "3 derniers mois"),
    ("semester", "Semestre"),
    ("year", "Année"),
    ("custom", "Plage personnalisée"),
]

PERIOD_LABELS = {key: label for key, label in PERIOD_CHOICES}


def _months_ago(timestamp, months):
    total_months = timestamp.year * 12 + timestamp.month - 1 - months
    year = total_months // 12
    month = total_months % 12 + 1
    return timestamp.replace(year=year, month=month, day=1)


def _start_of_day(timestamp):
    return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)


def _time_range_for_period(period, reference):
    end = reference
    if period == "today":
        start = _start_of_day(reference)
    elif period == "yesterday":
        today_start = _start_of_day(reference)
        start = today_start - timedelta(days=1)
        end = today_start - timedelta(microseconds=1)
    elif period == "day":
        start = _start_of_day(reference)
    elif period == "week":
        start = _start_of_day(reference - timedelta(days=reference.weekday()))
    elif period == "month":
        start = _start_of_day(reference.replace(day=1))
    elif period == "3months":
        start = _start_of_day(_months_ago(reference, 2))
    elif period == "semester":
        start = _start_of_day(_months_ago(reference, 5))
    elif period == "year":
        start = _start_of_day(_months_ago(reference, 11))
    else:
        start = _start_of_day(reference.replace(day=1))
    return start, end


def _build_custom_range(start, end):
    if not start or not end:
        return None
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return None
    if start_date > end_date:
        return None
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max), tz)
    return start_dt, end_dt



def _parse_list_date_range(start_value, end_value):
    tz = timezone.get_current_timezone()
    start_dt = None
    end_dt = None
    errors: list[str] = []
    if start_value:
        try:
            start_date = datetime.strptime(start_value, "%Y-%m-%d").date()
        except ValueError:
            errors.append("Date de debut invalide.")
        else:
            start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    if end_value:
        try:
            end_date = datetime.strptime(end_value, "%Y-%m-%d").date()
        except ValueError:
            errors.append("Date de fin invalide.")
        else:
            end_dt = timezone.make_aware(datetime.combine(end_date, time.max), tz)
    if start_dt and end_dt and start_dt > end_dt:
        errors.append("La date de debut doit precéder la date de fin.")
    return start_dt, end_dt, errors


def _get_return_url(request, default_name):
    return (
        request.GET.get("return")
        or request.META.get("HTTP_REFERER")
        or reverse(default_name)
    )


def dashboard(request):
    site_context = _site_context(request)
    active_site = site_context["active_site"]
    tz = timezone.get_current_timezone()
    products = (
        Product.objects.with_stock_quantity(site=active_site)
        .select_related("brand", "category")
    )
    aggregates = products.aggregate(
        total_stock=Coalesce(Sum("current_stock"), Value(0)),
    )
    total_products = products.count()
    low_stock = (
        products.filter(minimum_stock__gt=0)
        .filter(current_stock__lt=F("minimum_stock"))
        .order_by("current_stock")[:6]
    )
    recent_movements = StockMovement.objects.with_direction()
    if active_site:
        recent_movements = recent_movements.filter(site=active_site)
    recent_movements = recent_movements[:6]
    movement_queryset = StockMovement.objects.all()
    if active_site:
        movement_queryset = movement_queryset.filter(site=active_site)
    totals_by_direction = (
        movement_queryset.values("movement_type__direction")
        .annotate(total=Coalesce(Sum("quantity"), Value(0)))
        .order_by()
    )
    decimal_zero_value = Value(
        0,
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    sales_amount_expression = ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    sale_item_amount_expression = ExpressionWrapper(
        F("items__quantity") * F("items__unit_price"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    site_breakdown = []
    for site in site_context["sites"]:
        site_products = Product.objects.with_stock_quantity(site=site)
        totals = (
            site_products.aggregate(total=Coalesce(Sum("current_stock"), Value(0)))
            .get("total")
            or 0
        )
        site_sales_items = SaleItem.objects.filter(
            sale__status=Sale.Status.CONFIRMED,
            stock_movement__site=site,
        )
        site_sales_count = (
            Sale.objects.filter(
                status=Sale.Status.CONFIRMED,
                items__stock_movement__site=site,
            )
            .distinct()
            .count()
        )
        site_sales_amount = (
            site_sales_items.aggregate(
                total=Coalesce(
                    Sum(sales_amount_expression), decimal_zero_value
                )
            )
            .get("total")
            or Decimal("0.00")
        )
        site_breakdown.append(
            {
                "site": site,
                "total_stock": totals,
                "product_count": site_products.count(),
                "movement_count": StockMovement.objects.filter(site=site).count(),
                "confirmed_sales_count": site_sales_count,
                "confirmed_sales_amount": site_sales_amount,
            }
        )
    confirmed_sales = Sale.objects.filter(status=Sale.Status.CONFIRMED)
    sales_amount = (
        SaleItem.objects.filter(sale__status=Sale.Status.CONFIRMED)
        .aggregate(
            total=Coalesce(
                Sum(sales_amount_expression),
                decimal_zero_value,
            )
        )
        .get("total") or Decimal("0.00")
    )
    return_amount_expression = ExpressionWrapper(
        F("returned_quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    returned_items = SaleItem.objects.filter(
        sale__status=Sale.Status.CONFIRMED,
        line_type=SaleItem.LineType.PRODUCT,
        returned_quantity__gt=0,
    )
    return_totals = returned_items.aggregate(
        total_quantity=Coalesce(Sum("returned_quantity"), Value(0)),
        total_amount=Coalesce(Sum(return_amount_expression), decimal_zero_value),
    )
    returns_summary = {
        "count": (
            Sale.objects.filter(
                status=Sale.Status.CONFIRMED,
                items__returned_quantity__gt=0,
            )
            .distinct()
            .count()
        ),
        "quantity": return_totals.get("total_quantity") or 0,
        "amount": return_totals.get("total_amount") or Decimal("0.00"),
    }
    customer_count = Customer.objects.count()
    movement_count = movement_queryset.count()

    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    current_week_end = current_week_start + timedelta(days=6)
    default_start = timezone.make_aware(datetime.combine(current_week_start, time.min), tz)
    default_end = timezone.make_aware(datetime.combine(current_week_end, time.max), tz)
    graph_start_input = request.GET.get("start") or default_start.date().isoformat()
    graph_end_input = request.GET.get("end") or default_end.date().isoformat()
    graph_start, graph_end, date_errors = _parse_list_date_range(
        graph_start_input, graph_end_input
    )
    if date_errors:
        graph_start, graph_end = default_start, default_end
    graph_site_id = request.GET.get("graph_site")
    graph_site = None
    if graph_site_id:
        try:
            graph_site = Site.objects.get(pk=graph_site_id)
        except Site.DoesNotExist:
            graph_site = None
    elif active_site:
        graph_site = active_site

    confirmed_sales_period = Sale.objects.filter(
        status=Sale.Status.CONFIRMED,
        sale_date__gte=graph_start,
        sale_date__lte=graph_end,
    )
    if graph_site:
        confirmed_sales_period = confirmed_sales_period.filter(site=graph_site)

    product_items = SaleItem.objects.filter(
        sale__in=confirmed_sales_period,
        line_type=SaleItem.LineType.PRODUCT,
        product__isnull=False,
    )
    top_products = list(
        product_items.values("product__name", "product__sku")
        .annotate(
            sold_quantity=Coalesce(Sum("quantity"), Value(0)),
            sold_amount=Coalesce(Sum(sales_amount_expression), decimal_zero_value),
        )
        .order_by("-sold_quantity", "-sold_amount")[:5]
    )

    site_names = ["Abobo", "Treichville", "Riviera"]
    sales_by_site_raw = (
        confirmed_sales_period.values("site__name")
        .annotate(
            total_amount=Coalesce(
                Sum(sale_item_amount_expression),
                decimal_zero_value,
            )
        )
        .order_by("site__name")
    )
    sales_by_site = {name: Decimal("0.00") for name in site_names}
    for entry in sales_by_site_raw:
        site_name = entry.get("site__name")
        if site_name in sales_by_site:
            sales_by_site[site_name] = entry.get("total_amount") or Decimal("0.00")

    payment_totals = confirmed_sales_period.aggregate(
        total_paid=Coalesce(Sum("amount_paid"), decimal_zero_value),
        total_invoiced=Coalesce(
            Sum(sale_item_amount_expression),
            decimal_zero_value,
        ),
    )
    outstanding_amount = max(
        payment_totals.get("total_invoiced") - payment_totals.get("total_paid"),
        Decimal("0.00"),
    )

    year_start = timezone.make_aware(
        datetime(today.year, 1, 1, 0, 0, 0), tz
    )
    top_customers_year = list(
        Sale.objects.filter(
            status=Sale.Status.CONFIRMED,
            sale_date__gte=year_start,
        )
        .values("customer__name", "customer__company_name", "customer_name")
        .annotate(
            total_amount=Coalesce(
                Sum(sale_item_amount_expression),
                decimal_zero_value,
            )
        )
        .order_by("-total_amount")[:5]
    )

    module_cards = [
        {
            "title": "Produits",
            "value": total_products,
            "hint": "Catalogue exploitable",
            "url": reverse("inventory:inventory_overview"),
        },
        {
            "title": "Mouvements de stock",
            "value": movement_count,
            "hint": "Historique cumulé",
            "url": reverse("inventory:record_movement"),
        },
        {
            "title": "Vente",
            "value": confirmed_sales.count(),
            "hint": f"{sales_amount} FCFA",
            "url": reverse("inventory:sales_list"),
        },
        {
            "title": "Compte client",
            "value": customer_count,
            "hint": "Clients enregistrés",
            "url": reverse("inventory:customer_list"),
        },
        {
            "title": "Analyse",
            "value": "Vue détaillée",
            "hint": "Performances & tendances",
            "url": reverse("inventory:analytics"),
        },
    ]
    context = {
        "total_products": total_products,
        "total_stock": aggregates["total_stock"],
        "recent_movements": recent_movements,
        "low_stock": low_stock,
        "totals_by_direction": {
            row["movement_type__direction"]: row["total"] for row in totals_by_direction
        },
        "sales_summary": {
            "count": confirmed_sales.count(),
            "amount": sales_amount,
        },
        "returns_summary": returns_summary,
        "customer_count": customer_count,
        "movement_count": movement_count,
        "site_breakdown": site_breakdown,
        "top_products": top_products,
        "graph_start_input": graph_start_input,
        "graph_end_input": graph_end_input,
        "graph_site": graph_site,
        "sales_by_site": sales_by_site,
        "payment_totals": payment_totals,
        "outstanding_amount": outstanding_amount,
        "top_customers_year": top_customers_year,
        "module_cards": module_cards,
    }
    context.update(site_context)
    return render(request, "inventory/dashboard.html", context)


def analytics(request):
    now = timezone.now()
    selected_period = request.GET.get("period") or "month"
    start, end = _time_range_for_period(selected_period, now)
    custom_start_value = request.GET.get("start")
    custom_end_value = request.GET.get("end")
    if selected_period == "custom":
        custom_range = _build_custom_range(custom_start_value, custom_end_value)
        if custom_range:
            start, end = custom_range
        else:
            messages.warning(
                request,
                "Plage personnalisée invalide : affichage sur le mois en cours.",
            )
            selected_period = "month"
            start, end = _time_range_for_period(selected_period, now)
    start_input = custom_start_value or start.date().isoformat()
    end_input = custom_end_value or end.date().isoformat()
    period_label = PERIOD_LABELS.get(selected_period, PERIOD_LABELS["month"])
    range_label = f"{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}"

    customers_qs = Customer.objects.filter(created_at__gte=start, created_at__lte=end).order_by(
        "-created_at"
    )
    customers = list(customers_qs[:6])
    customers_count = customers_qs.count()

    confirmed_sales_qs = (
        Sale.objects.filter(status=Sale.Status.CONFIRMED, sale_date__gte=start, sale_date__lte=end)
        .select_related("customer")
        .prefetch_related("items__product")
        .order_by("-sale_date")
    )
    confirmed_sales = list(confirmed_sales_qs[:6])
    confirmed_sales_count = confirmed_sales_qs.count()

    quotes_qs = (
        Sale.objects.filter(status=Sale.Status.DRAFT, sale_date__gte=start, sale_date__lte=end)
        .select_related("customer")
        .order_by("-sale_date")
    )
    quotes = list(quotes_qs[:6])
    quotes_count = quotes_qs.count()


    amount_expression = ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    decimal_field = DecimalField(max_digits=14, decimal_places=2)
    product_items = SaleItem.objects.filter(
        sale__status=Sale.Status.CONFIRMED,
        sale__sale_date__gte=start,
        sale__sale_date__lte=end,
        line_type=SaleItem.LineType.PRODUCT,
        product__isnull=False,
    )
    sales_totals = product_items.aggregate(
        total_amount=Coalesce(
            Sum(amount_expression),
            Value(Decimal("0.00"), output_field=decimal_field),
        ),
        total_quantity=Coalesce(Sum("quantity"), Value(0)),
    )
    distinct_products = product_items.values("product_id").distinct().count()
    sold_products = list(
        product_items.values("product_id", "product__name", "product__sku")
        .annotate(
            sold_quantity=Coalesce(Sum("quantity"), Value(0)),
            sold_amount=Coalesce(
                Sum(amount_expression),
                Value(Decimal("0.00"), output_field=decimal_field),
            ),
        )
        .order_by("-sold_quantity", "-sold_amount")
        [:20]
    )
    reception_entries = list(
        StockMovement.objects.filter(
            movement_date__gte=start,
            movement_date__lte=end,
            movement_type__direction=MovementType.MovementDirection.ENTRY,
        )
        .values("product_id", "product__name", "product__sku")
        .annotate(received_quantity=Coalesce(Sum("quantity"), Value(0)))
    )

    performance_map: dict[int, dict[str, object]] = {}
    for row in sold_products:
        performance_map[row["product_id"]] = {
            "product_id": row["product_id"],
            "name": row["product__name"],
            "sku": row["product__sku"],
            "sold_quantity": int(row["sold_quantity"]),
            "sold_amount": row["sold_amount"],
            "received_quantity": 0,
        }

    for entry in reception_entries:
        perf = performance_map.setdefault(
            entry["product_id"],
            {
                "product_id": entry["product_id"],
                "name": entry["product__name"],
                "sku": entry["product__sku"],
                "sold_quantity": 0,
                "sold_amount": Decimal("0.00"),
                "received_quantity": 0,
            },
        )
        perf["received_quantity"] = int(entry["received_quantity"])
        perf.setdefault("name", entry["product__name"])
        perf.setdefault("sku", entry["product__sku"])

    product_performance = sorted(
        performance_map.values(),
        key=lambda record: (
            record["sold_quantity"],
            record["received_quantity"],
            record["sold_amount"],
        ),
        reverse=True,
    )[:6]

    cards = [
        {
            "title": "Clients",
            "value": customers_count,
            "unit": "nouveaux",
            "url": reverse("inventory:customer_list"),
            "description": "Clients enregistrés pendant la période",
        },
        {
            "title": "Factures",
            "value": confirmed_sales_count,
            "unit": "confirmées",
            "amount": sales_totals["total_amount"],
            "url": reverse("inventory:sales_list"),
            "description": "Ventes validées et converties en facture",
        },
        {
            "title": "Produits",
            "value": distinct_products,
            "unit": "références vendues",
            "url": reverse("inventory:inventory_overview"),
            "description": "Références différentes concernées par les ventes",
        },
        {
            "title": "Ventes",
            "value": sales_totals["total_quantity"],
            "unit": "articles vendus",
            "url": reverse("inventory:sales_list"),
            "description": "Quantité totale d'articles écoulés",
        },
        {
            "title": "Devis",
            "value": quotes_count,
            "unit": "devis",
            "url": reverse("inventory:quotes_list"),
            "description": "Devis créés pendant la période",
        },
    ]

    context = {
        "period_choices": PERIOD_CHOICES,
        "selected_period": selected_period,
        "start_input": start_input,
        "end_input": end_input,
        "period_label": period_label,
        "range_label": range_label,
        "customers": customers,
        "confirmed_sales": confirmed_sales,
        "quotes": quotes,
        "product_performance": product_performance,
        "cards": cards,
        "sales_totals": sales_totals,
        "customers_count": customers_count,
        "confirmed_sales_count": confirmed_sales_count,
        "quotes_count": quotes_count,
    }
    return render(request, "inventory/analytics.html", context)


def customers_list(request):
    search = (request.GET.get("q") or "").strip()
    customers = Customer.objects.with_balance()
    if search:
        customers = customers.filter(
            Q(name__icontains=search)
            | Q(company_name__icontains=search)
            | Q(reference__icontains=search)
            | Q(email__icontains=search)
            | Q(phone__icontains=search)
        )
    customers = customers.order_by("name", "company_name")
    customer_list = list(customers)
    outstanding_total = sum(
        (customer.balance for customer in customer_list if customer.balance > 0),
        Decimal("0.00"),
    )
    credit_total = sum(
        (-customer.balance for customer in customer_list if customer.balance < 0),
        Decimal("0.00"),
    )

    paginator = Paginator(customer_list, 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    pagination_query = query_params.urlencode()
    context = {
        "customers": page_obj,
        "page_obj": page_obj,
        "pagination_query": pagination_query,
        "search": search,
        "total_customers": paginator.count,
        "outstanding_total": outstanding_total,
        "credit_total": credit_total,
    }
    return render(request, "inventory/customers_list.html", context)


def customer_create(request):
    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save(commit=False)
            customer._history_user = (
                request.user if request.user.is_authenticated else None
            )
            customer.save()
            messages.success(request, "Le client a bien été créé.")
            return redirect(reverse("inventory:customer_detail", args=[customer.pk]))
    else:
        form = CustomerForm()
    return render(
        request,
        "inventory/customer_form.html",
        {
            "form": form,
            "title": "Nouveau client",
            "breadcrumb": "Ajouter un client",
        },
    )


def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            updated = form.save(commit=False)
            updated._history_user = (
                request.user if request.user.is_authenticated else None
            )
            updated.save()
            messages.success(request, "Le client a été mis à jour.")
            return redirect(reverse("inventory:customer_detail", args=[customer.pk]))
    else:
        form = CustomerForm(instance=customer)
    return render(
        request,
        "inventory/customer_form.html",
        {
            "form": form,
            "title": customer.display_name,
            "breadcrumb": "Modifier le client",
            "customer": customer,
        },
    )


def customer_detail(request, pk):
    customer = get_object_or_404(Customer.objects.with_balance(), pk=pk)
    form_type = request.POST.get("form_type") if request.method == "POST" else None
    entry_form = CustomerAccountEntryForm(
        request.POST if request.method == "POST" and form_type != "customer" else None
    )
    customer_form = CustomerForm(
        request.POST if request.method == "POST" and form_type == "customer" else None,
        instance=customer,
    )
    versions = list(Version.for_instance(customer)[:12])
    if request.method == "POST":
        if form_type == "customer" and customer_form.is_valid():
            updated = customer_form.save(commit=False)
            updated._history_user = (
                request.user if request.user.is_authenticated else None
            )
            updated.save()
            messages.success(request, "Les informations du client ont été mises à jour.")
            return redirect(reverse("inventory:customer_detail", args=[customer.pk]))
        if entry_form.is_valid():
            entry = entry_form.save(commit=False)
            entry.customer = customer
            entry.save()
            if entry.entry_type == CustomerAccountEntry.EntryType.DEBIT:
                messages.success(request, "Débit enregistré sur le compte du client.")
            else:
                messages.success(request, "Crédit enregistré sur le compte du client.")
            return redirect(reverse("inventory:customer_detail", args=[customer.pk]))
    entries = list(customer.entries.select_related("sale").order_by("-occurred_at", "-id"))
    running_balance = customer.balance
    for entry in entries:
        entry.running_balance = running_balance
        running_balance -= entry.signed_amount
    context = {
        "customer": customer,
        "entries": entries,
        "entry_form": entry_form,
        "customer_form": customer_form,
        "versions": versions,
    }
    return render(request, "inventory/customer_detail.html", context)


def record_movement(request):
    site_context = _site_context(request)
    view_site = site_context["active_site"]
    action_site = site_context["action_site"]
    site_locked = bool(action_site and not request.user.is_superuser)
    MovementLineFormSet = formset_factory(  # type: ignore
        MovementLineForm,
        extra=1,
        can_delete=True,
    )
    if request.method == "POST":
        post_data = request.POST.copy()
        if (
            "product" in post_data
            and "quantity" in post_data
            and "lines-TOTAL_FORMS" not in post_data
        ):
            # Support single-line submissions (e.g., simple POST requests) by
            # adapting them to the expected formset structure.
            post_data["lines-TOTAL_FORMS"] = "1"
            post_data["lines-INITIAL_FORMS"] = "0"
            post_data["lines-MIN_NUM_FORMS"] = "0"
            post_data["lines-MAX_NUM_FORMS"] = "1000"
            post_data["lines-0-product"] = post_data.pop("product")
            post_data["lines-0-quantity"] = post_data.pop("quantity")
        header_form = MovementHeaderForm(
            post_data,
            current_site=action_site or view_site,
            site_locked=site_locked,
            user=request.user,
        )
        line_formset = MovementLineFormSet(post_data, prefix="lines")
        if header_form.is_valid() and line_formset.is_valid():
            line_forms = [
                form
                for form in line_formset
                if form.cleaned_data.get("product")
                and form.cleaned_data.get("quantity")
                and not form.cleaned_data.get("DELETE")
            ]
            if not line_forms:
                messages.error(request, "Ajoutez au moins un produit ? d?placer.")
            else:
                with transaction.atomic():
                    created = 0
                    history_user = request.user if request.user.is_authenticated else None
                    for form in line_forms:
                        movement = StockMovement(
                            product=form.cleaned_data["product"],
                            movement_type=header_form.cleaned_data["movement_type"],
                            quantity=form.cleaned_data["quantity"],
                            site=header_form.cleaned_data["site"],
                            movement_date=header_form.cleaned_data["movement_date"],
                            document_number=header_form.cleaned_data.get("document_number", ""),
                            comment=header_form.cleaned_data.get("comment", ""),
                            performed_by=history_user,
                        )
                        movement._history_user = history_user
                        movement.save()
                        created += 1
                messages.success(request, f"{created} mouvement(s) ont été enregistrés avec succès.")
                return redirect(reverse("inventory:dashboard"))
        elif "product" in request.POST and "lines-TOTAL_FORMS" not in request.POST:
            single_form = StockMovementForm(request.POST)
            if site_locked and (action_site or view_site):
                allowed_site = action_site or view_site
                single_form.fields["site"].queryset = Site.objects.filter(pk=allowed_site.pk)
            if single_form.is_valid():
                movement = single_form.save(commit=False)
                movement.performed_by = request.user if request.user.is_authenticated else None
                movement._history_user = request.user if request.user.is_authenticated else None
                movement.save()
                messages.success(request, "Mouvement enregistré avec succès.")
                return redirect(reverse("inventory:dashboard"))
    else:
        header_form = MovementHeaderForm(
            current_site=action_site or view_site,
            site_locked=site_locked,
            user=request.user,
        )
        line_formset = MovementLineFormSet(prefix="lines")
    product_dataset = [
        {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "image_url": product.image.url if product.image else "",
        }
        for product in Product.objects.order_by("name")
    ]
    context = {
        "header_form": header_form,
        "line_formset": line_formset,
        "product_dataset": product_dataset,
    }
    context.update(site_context)
    return render(request, "inventory/movement_form.html", context)

def inventory_overview(request):
    site_context = _site_context(request)
    view_site = site_context["active_site"]
    action_site = site_context["action_site"]
    site_locked = bool(action_site and not request.user.is_superuser)
    products = Product.objects.select_related("brand", "category")
    search = (request.GET.get("q") or "").strip()
    if search:
        search_query = Q()
        for term in search.split():
            token = Q(name__icontains=term) | Q(sku__icontains=term) | Q(
                manufacturer_reference__icontains=term
            )
            search_query &= token
        products = products.filter(search_query)
    brand_id = request.GET.get("brand")
    if brand_id:
        products = products.filter(brand_id=brand_id)
    category_id = request.GET.get("category")
    if category_id:
        products = products.filter(category_id=category_id)
    scan_code = request.GET.get("scan")
    scan_message = None
    if scan_code:
        filtered = products.for_scan_code(scan_code)
        if filtered.exists():
            products = filtered
            scan_message = f"Résultat du scan : {scan_code}"
        else:
            scan_message = (
                "Produit introuvable pour ce code. Veuillez le créer depuis la page Produits."
            )
            messages.error(
                request,
                "Aucun produit ne correspond à ce scan. Créez-le depuis la page d'ajout de produit avant de poursuivre.",
            )

    products = products.with_stock_quantity(site=view_site)

    sort_param = request.GET.get("sort") or "name"
    sort_options = {
        "name": ("name",),
        "price_desc": ("-sale_price", "name"),
        "price_asc": ("sale_price", "name"),
        "stock_desc": ("-current_stock", "name"),
        "newest": ("-created_at",),
    }
    sort_choice = sort_param if sort_param in sort_options else "name"

    adjustment_form = InventoryAdjustmentForm(
        request.POST or None, current_site=action_site or view_site, site_locked=site_locked
    )
    if request.method == "POST" and adjustment_form.is_valid():
        product = adjustment_form.cleaned_data["product"]
        counted_quantity = adjustment_form.cleaned_data["counted_quantity"]
        comment = adjustment_form.cleaned_data["comment"]
        stock_site = action_site if site_locked else view_site
        product_with_stock = Product.objects.with_stock_quantity(site=stock_site).get(
            pk=product.pk
        )
        difference = counted_quantity - product_with_stock.stock_quantity
        if difference == 0:
            messages.info(request, "Aucun écart détecté pour ce produit.")
            return redirect(reverse("inventory:inventory_overview"))
        movement_type = _get_adjustment_movement_type(difference > 0)
        if movement_type is None:
            messages.error(request, "Aucun type de mouvement d'ajustement disponible.")
            return redirect(reverse("inventory:inventory_overview"))
        adjustment_site = adjustment_form.cleaned_data["site"]
        adjustment = StockMovement(
            product=product,
            movement_type=movement_type,
            quantity=abs(difference),
            comment=comment or "Ajustement inventaire",
            site=adjustment_site,
            performed_by=request.user if request.user.is_authenticated else None,
        )
        adjustment._history_user = request.user if request.user.is_authenticated else None
        adjustment.save()
        messages.success(request, "Ajustement d'inventaire enregistré.")
        return redirect(reverse("inventory:inventory_overview"))

    products = products.order_by(*sort_options[sort_choice])

    page_size_param = request.GET.get("page_size")
    allowed_page_sizes = [12, 24, 48]
    try:
        page_size = int(page_size_param) if page_size_param else allowed_page_sizes[0]
    except ValueError:
        page_size = allowed_page_sizes[0]
    if page_size not in allowed_page_sizes:
        page_size = allowed_page_sizes[0]

    paginator = Paginator(products, page_size)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    _attach_site_stocks(page_obj.object_list)
    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    pagination_query = query_params.urlencode()

    product_dataset = [
        {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "barcode": product.barcode,
            "image_url": product.image.url if product.image else "",
            "brand": getattr(product.brand, "name", ""),
            "category": getattr(product.category, "name", ""),
        }
        for product in Product.objects.select_related("brand", "category").order_by("name")
    ]

    context = {
        "products": page_obj,
        "page_obj": page_obj,
        "pagination_query": pagination_query,
        "adjustment_form": adjustment_form,
        "search": search or "",
        "scan_code": scan_code or "",
        "scan_message": scan_message,
        "selected_sort": sort_choice,
        "sort_options": sort_options,
        "page_size": page_size,
        "allowed_page_sizes": allowed_page_sizes,
        "brands": Product.objects.values("brand_id", "brand__name").distinct().order_by(
            "brand__name"
        ),
        "categories": Product.objects.values("category_id", "category__name").distinct().order_by(
            "category__name"
        ),
        "selected_brand": brand_id or "",
        "selected_category": category_id or "",
        "total_products": paginator.count,
        "product_dataset": product_dataset,
    }
    context.update(site_context)
    return render(request, "inventory/inventory_list.html", context)


def stock_valuation(request):
    site_context = _site_context(request)
    include_negative = request.GET.get("include_negative") == "1"
    use_sale_fallback = request.GET.get("use_sale_fallback", "1") == "1"
    signed_quantity = Case(
        When(
            movement_type__direction=MovementType.MovementDirection.ENTRY,
            then=F("quantity"),
        ),
        When(
            movement_type__direction=MovementType.MovementDirection.EXIT,
            then=-F("quantity"),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )
    aggregates = (
        StockMovement.objects.select_related("site", "product")
        .values(
            "site_id",
            "site__name",
            "product_id",
            "product__sku",
            "product__name",
            "product__purchase_price",
            "product__sale_price",
        )
        .annotate(
            quantity=Coalesce(
                Sum(signed_quantity),
                Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("site__name", "product__name")
    )
    site_totals = {}
    for site in site_context["sites"]:
        site_totals[site.pk] = {
            "site": site,
            "total_quantity": 0,
            "total_value": Decimal("0.00"),
            "negative_count": 0,
            "missing_purchase_count": 0,
            "missing_price_count": 0,
        }
    detail_rows = []
    for entry in aggregates:
        quantity = entry["quantity"] or 0
        if quantity == 0:
            continue
        purchase_price = entry["product__purchase_price"]
        sale_price = entry["product__sale_price"]
        unit_price = purchase_price
        price_source = "Achat"
        if unit_price is None:
            if use_sale_fallback and sale_price is not None:
                unit_price = sale_price
                price_source = "Vente"
            else:
                unit_price = None
                price_source = "Manquant"
        negative_stock = quantity < 0
        quantity_for_value = quantity
        if negative_stock and not include_negative:
            quantity_for_value = 0
        value = (
            Decimal(quantity_for_value) * unit_price
            if unit_price is not None
            else Decimal("0.00")
        )
        site_data = site_totals.get(entry["site_id"])
        if site_data is None:
            continue
        site_data["total_quantity"] += quantity_for_value
        site_data["total_value"] += value
        if negative_stock:
            site_data["negative_count"] += 1
        if purchase_price is None:
            site_data["missing_purchase_count"] += 1
        if unit_price is None:
            site_data["missing_price_count"] += 1
        detail_rows.append(
            {
                "site_name": entry["site__name"],
                "product_sku": entry["product__sku"],
                "product_name": entry["product__name"],
                "quantity": quantity,
                "quantity_for_value": quantity_for_value,
                "purchase_price": purchase_price,
                "sale_price": sale_price,
                "unit_price": unit_price,
                "price_source": price_source,
                "value": value,
                "negative_stock": negative_stock,
                "missing_purchase": purchase_price is None,
            }
        )
    context = {
        "include_negative": include_negative,
        "use_sale_fallback": use_sale_fallback,
        "site_totals": list(site_totals.values()),
        "detail_rows": detail_rows,
    }
    context.update(site_context)
    return render(request, "inventory/stock_valuation.html", context)


def inventory_physical(request):
    site_context = _site_context(request)
    view_site = site_context["active_site"]
    action_site = site_context["action_site"]
    site_locked = bool(action_site and not request.user.is_superuser)
    current_site = action_site or view_site
    if current_site is None:
        messages.error(
            request,
            "Sélectionnez un site pour lancer un inventaire physique.",
        )
        return redirect(reverse("inventory:inventory_overview"))

    session = (
        InventoryCountSession.objects.filter(status=InventoryCountSession.Status.OPEN, site=current_site)
        .order_by("-started_at")
        .select_related("site", "created_by")
        .first()
    )
    products_snapshot = (
        Product.objects.with_stock_quantity(site=current_site)
        .select_related("brand", "category")
        .order_by("name")
    )
    if session is None:
        session = InventoryCountSession.objects.create(
            name=f"Inventaire du {timezone.now().strftime('%d/%m/%Y %H:%M')}",
            site=current_site,
            created_by=request.user if request.user.is_authenticated else None,
        )
        missing_products = products_snapshot
    else:
        existing_product_ids = set(
            session.lines.values_list("product_id", flat=True)
        )
        missing_products = products_snapshot.exclude(pk__in=existing_product_ids)

    if missing_products:
        lines = []
        for product in missing_products:
            initial_qty = product.current_stock or 0
            lines.append(
                InventoryCountLine(
                    session=session,
                    product=product,
                    expected_qty=initial_qty,
                    counted_qty=initial_qty,
                    difference=0,
                    value_loss=Decimal("0.00"),
                )
            )
        InventoryCountLine.objects.bulk_create(lines)

    all_lines_qs = session.lines.select_related(
        "product",
        "product__brand",
        "product__category",
    ).order_by("product__name")

    search = (request.GET.get("q") or "").strip()
    show_only_differences = request.GET.get("diff_only") == "1"

    lines_qs = all_lines_qs
    if search:
        search_query = Q()
        for term in search.split():
            token = (
                Q(product__name__icontains=term)
                | Q(product__sku__icontains=term)
                | Q(product__barcode__icontains=term)
                | Q(product__brand__name__icontains=term)
                | Q(product__category__name__icontains=term)
            )
            search_query &= token
        lines_qs = lines_qs.filter(search_query)
    if show_only_differences:
        lines_qs = lines_qs.exclude(difference=0)

    if request.method == "POST" and not session.is_closed:
        action = request.POST.get("action", "save")
        updated = 0
        with transaction.atomic():
            for line in all_lines_qs:
                field_name = f"counted_{line.id}"
                if field_name not in request.POST:
                    continue
                try:
                    counted_value = int(request.POST.get(field_name, line.counted_qty))
                except (TypeError, ValueError):
                    counted_value = line.counted_qty
                if counted_value < 0:
                    counted_value = 0
                if counted_value != line.counted_qty:
                    line.counted_qty = counted_value
                    line.recompute()
                    line.save(update_fields=["counted_qty", "difference", "value_loss", "updated_at"])
                    updated += 1

            if action == "close":
                adjustments = []
                for line in all_lines_qs:
                    # recompute before clôture in case the loop above did not run (no changes)
                    line.recompute()
                    if line.difference == 0:
                        continue
                    movement_type = _get_adjustment_movement_type(line.difference > 0)
                    if movement_type is None:
                        messages.error(request, "Aucun type de mouvement d'ajustement disponible.")
                        return redirect(reverse("inventory:inventory_physical"))
                    movement = StockMovement(
                        product=line.product,
                        movement_type=movement_type,
                        quantity=abs(line.difference),
                        site=current_site,
                        comment=f"Inventaire {session.name}",
                        performed_by=request.user if request.user.is_authenticated else None,
                    )
                    movement._history_user = (
                        request.user if request.user.is_authenticated else None
                    )
                    adjustments.append(movement)
                if adjustments:
                    for movement in adjustments:
                        movement.save()
                session.status = InventoryCountSession.Status.CLOSED
                session.closed_at = timezone.now()
                session.save(update_fields=["status", "closed_at", "updated_at"])
                messages.success(request, "Inventaire clôturé et ajustements enregistrés.")
                return redirect(reverse("inventory:inventory_physical"))
        if updated:
            messages.success(request, f"{updated} ligne(s) mises à jour.")
        else:
            messages.info(request, "Aucune ligne mise à jour.")
        return redirect(reverse("inventory:inventory_physical"))

    totals = lines_qs.aggregate(
        total_difference=Coalesce(Sum("difference"), Value(0)),
        total_loss=Coalesce(Sum("value_loss"), Value(Decimal("0.00")), output_field=DecimalField(max_digits=14, decimal_places=2)),
    )

    overall_totals = all_lines_qs.aggregate(
        total_difference=Coalesce(Sum("difference"), Value(0)),
        total_loss=Coalesce(
            Sum("value_loss"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )

    product_dataset = [
        {
            "id": line.product.id,
            "name": line.product.name,
            "sku": line.product.sku,
            "barcode": line.product.barcode,
            "brand": getattr(line.product.brand, "name", ""),
            "category": getattr(line.product.category, "name", ""),
        }
        for line in all_lines_qs
    ]

    context = {
        "session": session,
        "lines": lines_qs,
        "site_locked": site_locked,
        "current_site": current_site,
        "search": search,
        "show_only_differences": show_only_differences,
        "total_lines": all_lines_qs.count(),
        "totals": totals,
        "overall_totals": overall_totals,
        "product_dataset": product_dataset,
    }
    context.update(site_context)
    return render(request, "inventory/inventory_physical.html", context)


def product_detail(request, pk):
    site_context = _site_context(request)
    active_site = site_context["active_site"]
    return_url = _get_return_url(request, "inventory:inventory_overview")
    product = get_object_or_404(
        Product.objects.with_stock_quantity(site=active_site).select_related("brand", "category"),
        pk=pk,
    )
    _attach_site_stocks([product])
    recent_movements = (
        product.stock_movements.select_related("movement_type")
        .order_by("-movement_date", "-id")
    )
    if active_site:
        recent_movements = recent_movements.filter(site=active_site)
    recent_movements = recent_movements[:8]
    versions = list(Version.for_instance(product).order_by("-created_at")[:12])
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            updated_product = form.save(commit=False)
            updated_product._history_user = (
                request.user if request.user.is_authenticated else None
            )
            updated_product.save()
            messages.success(request, "Les informations du produit ont été mises à jour.")
            return redirect(reverse("inventory:product_detail", args=[product.pk]))
    else:
        form = ProductForm(instance=product)
    context = {
        "product": product,
        "recent_movements": recent_movements,
        "form": form,
        "versions": versions,
        "return_url": return_url,
    }
    context.update(site_context)
    return render(request, "inventory/product_detail.html", context)


def product_create(request):
    site_context = _site_context(request)
    return_url = _get_return_url(request, "inventory:inventory_overview")
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save(commit=False)
            product._history_user = request.user if request.user.is_authenticated else None
            product.save()
            messages.success(request, "Produit créé avec succès.")
            return redirect(reverse("inventory:product_detail", args=[product.pk]))
    else:
        form = ProductForm()
    context = {
        "form": form,
        "return_url": return_url,
    }
    context.update(site_context)
    return render(request, "inventory/product_form.html", context)


def version_revert(request, version_id):
    version = get_object_or_404(Version, pk=version_id)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    restored = version.restore(
        user=request.user if request.user.is_authenticated else None
    )
    if restored is None:
        messages.error(request, "Impossible de restaurer cette version.")
        redirect_url = request.META.get("HTTP_REFERER") or reverse("inventory:inventory_overview")
        return redirect(redirect_url)
    messages.success(request, "La version a été restaurée.")
    redirect_url = version.object_url or getattr(
        restored, "get_absolute_url", None
    )
    if not redirect_url:
        redirect_url = request.META.get("HTTP_REFERER") or reverse("inventory:inventory_overview")
    return redirect(redirect_url)


def sales_list(request):
    search = (request.GET.get("q") or "").strip()
    selected_status = request.GET.get("status") or ""
    start_input = request.GET.get("start")
    end_input = request.GET.get("end")
    start_dt, end_dt, date_errors = _parse_list_date_range(start_input, end_input)
    for error_text in date_errors:
        messages.warning(request, error_text)
    site_context = _site_context(request)
    active_site = site_context["active_site"]
    sales_queryset = (
        Sale.objects.select_related("customer")
        .prefetch_related("items__product", "scans")
        .order_by("-sale_date")
    )
    if selected_status:
        sales_queryset = sales_queryset.filter(status=selected_status)
    if search:
        sales_queryset = sales_queryset.filter(
            Q(reference__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer__name__icontains=search)
            | Q(customer__company_name__icontains=search)
        )
    if start_dt:
        sales_queryset = sales_queryset.filter(sale_date__gte=start_dt)
    if end_dt:
        sales_queryset = sales_queryset.filter(sale_date__lte=end_dt)
    if active_site:
        sales_queryset = sales_queryset.filter(
            Q(site=active_site) | Q(items__stock_movement__site=active_site)
        )
    sales_queryset = sales_queryset.distinct()
    status_counts = {
        row["status"]: row["count"]
        for row in sales_queryset.values("status").annotate(count=Count("id"))
    }
    sales = list(sales_queryset)
    total_amount = Decimal("0.00")
    total_quantity = 0
    for sale in sales:
        sale_quantity = 0
        for item in sale.items.all():
            if item.line_type == SaleItem.LineType.PRODUCT and item.product:
                total_amount += item.total_amount
                sale_quantity += max(item.quantity - item.returned_quantity, 0)
        sale.total_quantity = sale_quantity
        sale.scan_total = sale.scans.count()
        total_quantity += sale_quantity
    total_return_quantity = sum(sale.returned_quantity for sale in sales)
    total_return_amount = sum(sale.returned_amount for sale in sales)
    status_summary = [
        {
            "value": status_choice.value,
            "label": status_choice.label,
            "count": status_counts.get(status_choice.value, 0),
        }
        for status_choice in Sale.Status
    ]
    status_options = [("", "Tous les statuts")] + [
        (status_choice.value, status_choice.label) for status_choice in Sale.Status
    ]
    context = {
        "sales": sales,
        "total_amount": total_amount,
        "total_quantity": total_quantity,
        "total_sales": len(sales),
        "total_return_quantity": total_return_quantity,
        "total_return_amount": total_return_amount,
        "search": search,
        "selected_status": selected_status,
        "start_input": start_input,
        "end_input": end_input,
        "status_summary": status_summary,
        "status_options": status_options,
    }
    context.update(site_context)
    return render(request, "inventory/sales_list.html", context)


def quotes_list(request):
    search = (request.GET.get("q") or "").strip()
    start_input = request.GET.get("start")
    end_input = request.GET.get("end")
    start_dt, end_dt, date_errors = _parse_list_date_range(start_input, end_input)
    for error_text in date_errors:
        messages.warning(request, error_text)
    site_context = _site_context(request)
    active_site = site_context["active_site"]
    quotes_queryset = (
        Sale.objects.filter(status=Sale.Status.DRAFT)
        .select_related("customer")
        .prefetch_related("items__product")
        .order_by("-sale_date")
    )
    if search:
        quotes_queryset = quotes_queryset.filter(
            Q(reference__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer__name__icontains=search)
            | Q(customer__company_name__icontains=search)
        )
    if active_site:
        quotes_queryset = quotes_queryset.filter(site=active_site)
    if start_dt:
        quotes_queryset = quotes_queryset.filter(sale_date__gte=start_dt)
    if end_dt:
        quotes_queryset = quotes_queryset.filter(sale_date__lte=end_dt)
    quotes = list(quotes_queryset)
    quote_count = len(quotes)
    total_amount = sum((quote.total_amount for quote in quotes), Decimal("0.00"))
    average_amount = (
        total_amount / Decimal(quote_count) if quote_count else Decimal("0.00")
    )
    context = {
        "quotes": quotes,
        "count": quote_count,
        "quote_count": quote_count,
        "total_amount": total_amount,
        "average_amount": average_amount,
        "search": search,
        "start_input": start_input,
        "end_input": end_input,
    }
    context.update(site_context)
    return render(request, "inventory/quotes_list.html", context)


def sale_create(request):
    action_site = _get_action_site(request)
    if request.method == "POST":
        sale_form = SaleForm(request.POST)
        formset = SaleItemFormSet(request.POST, prefix="items")
        if sale_form.is_valid() and formset.is_valid():
            cleaned_lines = []
            sale_date = sale_form.cleaned_data["sale_date"]
            for position, form in enumerate(formset):
                if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                    continue
                line_type = form.cleaned_data.get("line_type") or SaleItem.LineType.PRODUCT
                description = form.cleaned_data.get("description") or ""
                scan_code = (form.cleaned_data.get("scan_code") or "").strip()
                product = form.cleaned_data.get("product")
                unit_price = form.cleaned_data.get("unit_price")
                quantity = form.cleaned_data.get("quantity") or 0
                scanned_at = sale_date if line_type == SaleItem.LineType.PRODUCT else None
                if unit_price is None and product:
                    unit_price = product.sale_price or Decimal("0.00")
                cleaned_lines.append(
                    {
                        "line_type": line_type,
                        "product": product if line_type == SaleItem.LineType.PRODUCT else None,
                        "quantity": quantity if line_type == SaleItem.LineType.PRODUCT else 0,
                        "unit_price": unit_price if line_type == SaleItem.LineType.PRODUCT else Decimal("0.00"),
                        "scan_code": scan_code if line_type == SaleItem.LineType.PRODUCT else "",
                        "scanned_at": scanned_at,
                        "description": description,
                        "position": position,
                    }
                )
            has_product_line = any(
                line["line_type"] == SaleItem.LineType.PRODUCT for line in cleaned_lines
            )
            if not has_product_line:
                messages.error(request, "Ajoutez au moins un produit à la vente.")
            total_amount = sum(
                (
                    line["unit_price"] * Decimal(line["quantity"])
                    for line in cleaned_lines
                    if line["line_type"] == SaleItem.LineType.PRODUCT
                ),
                Decimal("0.00"),
            )
            amount_paid = sale_form.cleaned_data.get("amount_paid") or Decimal("0.00")
            if amount_paid > total_amount:
                sale_form.add_error(
                    "amount_paid",
                    "Le paiement ne peut pas dépasser le total de la vente.",
                )
            if has_product_line and amount_paid <= total_amount:
                with transaction.atomic():
                    sale = _save_sale_with_customer(
                        sale_form,
                        history_user=request.user if request.user.is_authenticated else None,
                        site=action_site,
                    )
                    items_to_create = []
                    for line in cleaned_lines:
                        item = SaleItem(
                            sale=sale,
                            line_type=line["line_type"],
                            description=line["description"],
                            position=line["position"],
                            quantity=line["quantity"],
                            unit_price=line["unit_price"],
                            scan_code=line["scan_code"],
                            scanned_at=line["scanned_at"],
                        )
                        if line["product"]:
                            item.product = line["product"]
                        items_to_create.append(item)
                    SaleItem.objects.bulk_create(items_to_create)
                    sale.confirm(
                        performed_by=request.user if request.user.is_authenticated else None,
                        site=action_site,
                    )
                messages.success(request, "La vente a été enregistrée et le stock mis à jour.")
                return redirect(reverse("inventory:sales_list"))
    else:
        sale_form = SaleForm(initial={"reference": _generate_sale_reference()})
        formset = SaleItemFormSet(
            prefix="items",
            initial=[{"line_type": SaleItem.LineType.PRODUCT}],
        )
    product_dataset = [
        {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "sale_price": float(product.sale_price) if product.sale_price is not None else 0,
            "image_url": product.image.url if product.image else "",
        }
        for product in Product.objects.order_by("name")
    ]
    customer_dataset = [
        {
            "id": customer.id,
            "display_name": customer.display_name,
            "reference": customer.reference,
            "phone": customer.phone,
        }
        for customer in Customer.objects.order_by("name", "company_name")
    ]
    context = {
        "sale_form": sale_form,
        "formset": formset,
        "product_dataset": product_dataset,
        "customer_dataset": customer_dataset,
        "form_title": "Nouvelle vente",
        "form_description": "Confirmez la vente et mettez le stock à jour.",
        "submit_label": "Enregistrer la vente",
        "cancel_url": reverse("inventory:sales_list"),
    }
    return render(request, "inventory/sale_form.html", context)


def sale_return(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related("customer", "site").prefetch_related("items__product"),
        pk=pk,
    )
    if sale.status != Sale.Status.CONFIRMED:
        messages.warning(
            request, "Seules les ventes confirmées peuvent enregistrer des retours."
        )
        return redirect(reverse("inventory:sales_list"))

    sale_items = list(
        sale.items.filter(
            line_type=SaleItem.LineType.PRODUCT,
            product__isnull=False,
        ).order_by("position", "id")
    )
    ReturnFormSet = formset_factory(SaleReturnItemForm, extra=0)
    initial_data = [
        {"sale_item_id": item.pk, "return_quantity": 0} for item in sale_items
    ]
    formset = ReturnFormSet(
        request.POST or None,
        prefix="returns",
        initial=initial_data,
    )
    processed_items: list[tuple[SaleItem, int]] = []
    if request.method == "POST" and formset.is_valid():
        sale_items_map = {item.pk: item for item in sale_items}
        has_errors = False
        for form in formset:
            sale_item_id = form.cleaned_data.get("sale_item_id")
            quantity = form.cleaned_data.get("return_quantity") or 0
            sale_item = sale_items_map.get(sale_item_id)
            if sale_item is None:
                form.add_error(None, "Ligne invalide.")
                has_errors = True
                continue
            if quantity > sale_item.available_return_quantity:
                form.add_error(
                    "return_quantity",
                    f"Vous ne pouvez retourner que {sale_item.available_return_quantity} exemplaire(s).",
                )
                has_errors = True
                continue
            if quantity <= 0:
                continue
            processed_items.append((sale_item, quantity))
        if not has_errors:
            if not processed_items:
                messages.warning(request, "Sélectionnez au moins un article à retourner.")
            else:
                movement_site = _get_action_site(request) or sale.site or get_default_site()
                if movement_site is None:
                    messages.error(
                        request, "Aucun site configuré pour enregistrer le retour."
                    )
                else:
                    movement_type = SaleItem._get_return_movement_type()
                    total_returned_quantity = sum(quantity for _, quantity in processed_items)
                    total_returned_amount = sum(
                        sale_item.unit_price * Decimal(quantity)
                        for sale_item, quantity in processed_items
                    )
                    try:
                        with transaction.atomic():
                            for sale_item, quantity in processed_items:
                                sale_item.record_return(
                                    quantity,
                                    performed_by=request.user if request.user.is_authenticated else None,
                                    movement_type=movement_type,
                                    site=movement_site,
                                )
                        messages.success(
                            request,
                            f"{total_returned_quantity} article(s) enregistrés en retour pour {total_returned_amount:.2f} FCFA.",
                        )
                        return redirect(reverse("inventory:sales_list"))
                    except (ValueError, RuntimeError) as exc:
                        messages.error(request, str(exc))

    form_rows = list(zip(formset.forms, sale_items))
    context = {
        "sale": sale,
        "formset": formset,
        "form_rows": form_rows,
        "available_return_total": sum(item.available_return_quantity for item in sale_items),
        "cancel_url": reverse("inventory:sales_list"),
    }
    return render(request, "inventory/sale_return.html", context)


def sale_adjust(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related("customer", "site").prefetch_related("items__product"),
        pk=pk,
    )
    if sale.status != Sale.Status.CONFIRMED:
        messages.warning(
            request, "Seules les ventes confirmées peuvent être ajustées."
        )
        return redirect(reverse("inventory:sales_list"))

    sale_items = list(
        sale.items.filter(
            line_type=SaleItem.LineType.PRODUCT,
            product__isnull=False,
        ).order_by("position", "id")
    )
    AdjustmentFormSet = formset_factory(SaleAdjustmentItemForm, extra=0)
    initial_data = [
        {
            "sale_item_id": item.pk,
            "keep_quantity": max(item.quantity - item.returned_quantity, 0),
            "unit_price": item.unit_price,
        }
        for item in sale_items
    ]
    formset = AdjustmentFormSet(
        request.POST or None,
        prefix="adjust",
        initial=initial_data,
    )
    form_rows = list(zip(formset.forms, sale_items))
    if request.method == "POST" and formset.is_valid():
        sale_items_map = {item.pk: item for item in sale_items}
        rows_to_process: list[tuple[SaleItem, int, Decimal]] = []
        has_errors = False
        for form in formset:
            sale_item_id = form.cleaned_data.get("sale_item_id")
            keep_quantity = form.cleaned_data.get("keep_quantity") or 0
            unit_price = form.cleaned_data.get("unit_price")
            sale_item = sale_items_map.get(sale_item_id)
            if sale_item is None:
                form.add_error(None, "Ligne invalide.")
                has_errors = True
                continue
            available_quantity = max(sale_item.quantity - sale_item.returned_quantity, 0)
            if keep_quantity > available_quantity:
                form.add_error(
                    "keep_quantity",
                    f"Au maximum {available_quantity} unité(s) peuvent rester facturées.",
                )
                has_errors = True
                continue
            rows_to_process.append((sale_item, keep_quantity, unit_price))
        if not has_errors:
            movement_site = _get_active_site(request) or sale.site or get_default_site()
            if movement_site is None:
                messages.error(
                    request, "Aucun site configuré pour enregistrer le retour."
                )
            else:
                movement_type = SaleItem._get_return_movement_type()
                total_returned_quantity = 0
                total_returned_amount = Decimal("0.00")
                price_updates = 0
                with transaction.atomic():
                    for sale_item, keep_quantity, unit_price in rows_to_process:
                        available_quantity = max(
                            sale_item.quantity - sale_item.returned_quantity, 0
                        )
                        additional_return_qty = available_quantity - keep_quantity
                        if unit_price is not None and unit_price != sale_item.unit_price:
                            sale_item.unit_price = unit_price
                            sale_item.save(update_fields=["unit_price"])
                            price_updates += 1
                        if additional_return_qty > 0:
                            sale_item.record_return(
                                additional_return_qty,
                                performed_by=request.user if request.user.is_authenticated else None,
                                movement_type=movement_type,
                                site=movement_site,
                            )
                            total_returned_quantity += additional_return_qty
                            total_returned_amount += sale_item.unit_price * Decimal(
                                additional_return_qty
                            )
                feedback_parts = []
                if price_updates:
                    feedback_parts.append(f"{price_updates} prix mis à jour")
                if total_returned_quantity:
                    feedback_parts.append(
                        f"{total_returned_quantity} article(s) retourné(s) pour {total_returned_amount:.2f} FCFA"
                    )
                if not feedback_parts:
                    feedback_parts.append("Aucun changement appliqué.")
                messages.success(request, " ; ".join(feedback_parts))
                return redirect(
                    reverse("inventory:sale_document_preview", args=[sale.pk, "invoice"])
                )

    context = {
        "sale": sale,
        "formset": formset,
        "form_rows": form_rows,
        "cancel_url": reverse("inventory:sales_list"),
    }
    return render(request, "inventory/sale_adjust.html", context)


def quote_create(request):
    active_site = _get_active_site(request)
    if request.method == "POST":
        sale_form = SaleForm(request.POST)
        formset = SaleItemFormSet(request.POST, prefix="items")
        if sale_form.is_valid() and formset.is_valid():
            cleaned_lines = []
            sale_date = sale_form.cleaned_data["sale_date"]
            for position, form in enumerate(formset):
                if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                    continue
                line_type = form.cleaned_data.get("line_type") or SaleItem.LineType.PRODUCT
                description = form.cleaned_data.get("description") or ""
                scan_code = (form.cleaned_data.get("scan_code") or "").strip()
                product = form.cleaned_data.get("product")
                unit_price = form.cleaned_data.get("unit_price")
                quantity = form.cleaned_data.get("quantity") or 0
                scanned_at = sale_date if line_type == SaleItem.LineType.PRODUCT else None
                if unit_price is None and product:
                    unit_price = product.sale_price or Decimal("0.00")
                cleaned_lines.append(
                    {
                        "line_type": line_type,
                        "product": product if line_type == SaleItem.LineType.PRODUCT else None,
                        "quantity": quantity if line_type == SaleItem.LineType.PRODUCT else 0,
                        "unit_price": unit_price if line_type == SaleItem.LineType.PRODUCT else Decimal("0.00"),
                        "scan_code": scan_code if line_type == SaleItem.LineType.PRODUCT else "",
                        "scanned_at": scanned_at,
                        "description": description,
                        "position": position,
                    }
                )
            has_product_line = any(
                line["line_type"] == SaleItem.LineType.PRODUCT for line in cleaned_lines
            )
            if not has_product_line:
                messages.error(request, "Ajoutez au moins un produit au devis.")
            if has_product_line:
                with transaction.atomic():
                    sale = _save_sale_with_customer(
                        sale_form,
                        status=Sale.Status.DRAFT,
                        history_user=request.user if request.user.is_authenticated else None,
                        site=active_site,
                    )
                    items_to_create = []
                    for line in cleaned_lines:
                        item = SaleItem(
                            sale=sale,
                            line_type=line["line_type"],
                            description=line["description"],
                            position=line["position"],
                            quantity=line["quantity"],
                            unit_price=line["unit_price"],
                            scan_code=line["scan_code"],
                            scanned_at=line["scanned_at"],
                        )
                        if line["product"]:
                            item.product = line["product"]
                        items_to_create.append(item)
                    SaleItem.objects.bulk_create(items_to_create)
                messages.success(request, "Le devis a été enregistré.")
                return redirect(reverse("inventory:quote_detail", args=[sale.pk]))
    else:
        sale_form = SaleForm(initial={"reference": _generate_sale_reference()})
        formset = SaleItemFormSet(
            prefix="items",
            initial=[{"line_type": SaleItem.LineType.PRODUCT}],
        )
    product_dataset = [
        {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "sale_price": float(product.sale_price) if product.sale_price is not None else 0,
            "image_url": product.image.url if product.image else "",
        }
        for product in Product.objects.order_by("name")
    ]
    customer_dataset = [
        {
            "id": customer.id,
            "display_name": customer.display_name,
            "reference": customer.reference,
            "phone": customer.phone,
        }
        for customer in Customer.objects.order_by("name", "company_name")
    ]
    context = {
        "sale_form": sale_form,
        "formset": formset,
        "product_dataset": product_dataset,
        "customer_dataset": customer_dataset,
        "form_title": "Nouveau devis",
        "form_description": "Préparez un devis (proforma) sans mouvement de stock.",
        "submit_label": "Enregistrer le devis",
        "cancel_url": reverse("inventory:quotes_list"),
        "is_quote": True,
    }
    return render(request, "inventory/sale_form.html", context)


def _save_sale_with_customer(
    form: SaleForm,
    status: str | None = None,
    history_user=None,
    site: Site | None = None,
) -> Sale:
    customer = form.cleaned_data.get("customer")
    customer_name = form.cleaned_data.get("customer_name")
    if not customer and customer_name:
        customer = Customer(name=customer_name)
        customer._history_user = history_user
        customer.save()
    instance = form.save(commit=False)
    if customer:
        instance.customer = customer
        instance.customer_name = customer_name or customer.display_name
    if status:
        instance.status = status
    if site is not None:
        instance.site = site
    instance._history_user = history_user
    instance.save()
    form.save_m2m()
    return instance


def quote_detail(request, pk):
    site_context = _site_context(request)
    sale = get_object_or_404(
        Sale.objects.select_related("customer").prefetch_related("items__product"),
        pk=pk,
    )
    return_url = _get_return_url(request, "inventory:quotes_list")
    items = sale.items.select_related("product").order_by("position", "id")
    context = {
        "sale": sale,
        "items": items,
        "can_confirm": sale.status != Sale.Status.CONFIRMED,
        "return_url": return_url,
    }
    context.update(site_context)
    return render(request, "inventory/quote_detail.html", context)


def quote_edit(request, pk):
    site_context = _site_context(request)
    active_site = site_context.get("active_site")
    sale = get_object_or_404(
        Sale.objects.select_related("customer").prefetch_related("items__product"),
        pk=pk,
    )
    if sale.status == Sale.Status.CONFIRMED:
        messages.error(request, "Ce devis a déjà été converti et ne peut plus être modifié.")
        return redirect(reverse("inventory:quote_detail", args=[sale.pk]))

    if request.method == "POST":
        sale_form = SaleForm(request.POST, instance=sale)
        formset = SaleItemFormSet(request.POST, prefix="items")
        if sale_form.is_valid() and formset.is_valid():
            cleaned_lines = []
            sale_date = sale_form.cleaned_data["sale_date"]
            for position, form in enumerate(formset):
                if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                    continue
                line_type = form.cleaned_data.get("line_type") or SaleItem.LineType.PRODUCT
                description = form.cleaned_data.get("description") or ""
                scan_code = (form.cleaned_data.get("scan_code") or "").strip()
                product = form.cleaned_data.get("product")
                unit_price = form.cleaned_data.get("unit_price")
                quantity = form.cleaned_data.get("quantity") or 0
                scanned_at = sale_date if line_type == SaleItem.LineType.PRODUCT else None
                if unit_price is None and product:
                    unit_price = product.sale_price or Decimal("0.00")
                cleaned_lines.append(
                    {
                        "line_type": line_type,
                        "product": product if line_type == SaleItem.LineType.PRODUCT else None,
                        "quantity": quantity if line_type == SaleItem.LineType.PRODUCT else 0,
                        "unit_price": unit_price if line_type == SaleItem.LineType.PRODUCT else Decimal("0.00"),
                        "scan_code": scan_code if line_type == SaleItem.LineType.PRODUCT else "",
                        "scanned_at": scanned_at,
                        "description": description,
                        "position": position,
                    }
                )
            has_product_line = any(
                line["line_type"] == SaleItem.LineType.PRODUCT for line in cleaned_lines
            )
            if not has_product_line:
                messages.error(request, "Ajoutez au moins un produit au devis.")
            if has_product_line:
                with transaction.atomic():
                    sale = _save_sale_with_customer(
                        sale_form,
                        status=Sale.Status.DRAFT,
                        history_user=request.user if request.user.is_authenticated else None,
                        site=active_site or sale.site,
                    )
                    sale.items.all().delete()
                    items_to_create = []
                    for line in cleaned_lines:
                        item = SaleItem(
                            sale=sale,
                            line_type=line["line_type"],
                            description=line["description"],
                            position=line["position"],
                            quantity=line["quantity"],
                            unit_price=line["unit_price"],
                            scan_code=line["scan_code"],
                            scanned_at=line["scanned_at"],
                        )
                        if line["product"]:
                            item.product = line["product"]
                        items_to_create.append(item)
                    SaleItem.objects.bulk_create(items_to_create)
                messages.success(request, "Le devis a été mis à jour.")
                return redirect(reverse("inventory:quote_detail", args=[sale.pk]))
    else:
        sale_form = SaleForm(instance=sale)
        initial_items = []
        for item in sale.items.select_related("product").order_by("position", "id"):
            initial_items.append(
                {
                    "line_type": item.line_type,
                    "product": item.product if item.line_type == SaleItem.LineType.PRODUCT else None,
                    "quantity": item.quantity if item.line_type == SaleItem.LineType.PRODUCT else 0,
                    "unit_price": item.unit_price if item.line_type == SaleItem.LineType.PRODUCT else Decimal("0.00"),
                    "scan_code": item.scan_code if item.line_type == SaleItem.LineType.PRODUCT else "",
                    "description": item.description,
                }
            )
        if not initial_items:
            initial_items = [{"line_type": SaleItem.LineType.PRODUCT}]
        formset = SaleItemFormSet(prefix="items", initial=initial_items)

    product_dataset = [
        {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "sale_price": float(product.sale_price) if product.sale_price is not None else 0,
            "image_url": product.image.url if product.image else "",
        }
        for product in Product.objects.order_by("name")
    ]
    customer_dataset = [
        {
            "id": customer.id,
            "display_name": customer.display_name,
            "reference": customer.reference,
            "phone": customer.phone,
        }
        for customer in Customer.objects.order_by("name", "company_name")
    ]
    context = {
        "sale_form": sale_form,
        "formset": formset,
        "product_dataset": product_dataset,
        "customer_dataset": customer_dataset,
        "form_title": f"Modifier le devis {sale.reference}",
        "form_description": "Ajustez les lignes du devis avant confirmation.",
        "submit_label": "Mettre à jour le devis",
        "cancel_url": reverse("inventory:quote_detail", args=[sale.pk]),
        "is_quote": True,
    }
    context.update(site_context)
    return render(request, "inventory/sale_form.html", context)


def quote_confirm(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method != "POST":
        return redirect(reverse("inventory:quote_detail", args=[sale.pk]))
    if sale.status != Sale.Status.CONFIRMED:
        sale.confirm(
            performed_by=request.user if request.user.is_authenticated else None,
            site=_get_active_site(request),
        )
        messages.success(request, "Devis converti en vente.")
    return redirect(reverse("inventory:sales_list"))


def sale_document_preview(request, pk, doc_type):
    sale = get_object_or_404(
        Sale.objects.select_related("customer").prefetch_related("items__product"),
        pk=pk,
    )
    return_url = _get_return_url(request, "inventory:sales_list")
    try:
        doc_meta = _get_document_meta(sale, doc_type)
    except Http404 as exc:
        messages.error(request, str(exc))
        return redirect(return_url)
    context = _build_document_context(sale, doc_meta) | {"return_url": return_url}
    return render(request, "inventory/document_preview.html", context)


def sale_document_pdf(request, pk, doc_type):
    sale = get_object_or_404(
        Sale.objects.select_related("customer").prefetch_related("items__product"),
        pk=pk,
    )
    return_url = _get_return_url(request, "inventory:sales_list")
    try:
        doc_meta = _get_document_meta(sale, doc_type)
    except Http404 as exc:
        messages.error(request, str(exc))
        return redirect(return_url)
    context = _build_document_context(sale, doc_meta) | {
        "pdf_export": True,
        "return_url": return_url,
    }
    html = render_to_string("inventory/document_preview.html", context, request=request)
    if HTML is None:  # pragma: no cover
        return HttpResponse(
            "WeasyPrint n'est pas installé. Installez-le avec 'pip install weasyprint' pour l'export PDF.",
            status=500,
        )
    response = HttpResponse(content_type="application/pdf")
    filename = f"{doc_meta['filename']}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf(response)
    return response


def _get_document_meta(sale: Sale, doc_type: str) -> dict:
    doc_type = (doc_type or "").lower()
    mapping = {
        "quote": {
            "title": "DEVIS",
            "subtitle": "Facture proforma",
            "requires_confirmed": False,
            "filename": f"devis-{sale.reference}",
        },
        "invoice": {
            "title": "FACTURE",
            "subtitle": "Vente confirmée",
            "requires_confirmed": True,
            "filename": f"facture-{sale.reference}",
        },
        "delivery": {
            "title": "BN DE LIVRAISON",
            "subtitle": "Produits livrés",
            "requires_confirmed": True,
            "filename": f"livraison-{sale.reference}",
        },
    }
    meta = mapping.get(doc_type)
    if not meta:
        raise Http404("Document inconnu")
    if meta["requires_confirmed"] and sale.status != Sale.Status.CONFIRMED:
        raise Http404("Document disponible après confirmation uniquement.")
    meta["doc_type"] = doc_type
    return meta


def _build_document_context(sale: Sale, doc_meta: dict) -> dict:
    items = list(sale.items.select_related("product").order_by("position", "id"))
    product_lines = [
        item for item in items if item.line_type == SaleItem.LineType.PRODUCT
    ]
    for item in product_lines:
        item.net_quantity = max(item.quantity - item.returned_quantity, 0)
        item.net_total_amount = item.unit_price * Decimal(item.net_quantity)
    total_amount = sum(item.net_total_amount for item in product_lines)
    balance = total_amount - (sale.amount_paid or Decimal("0.00"))
    return {
        "sale": sale,
        "items": items,
        "product_lines": product_lines,
        "doc_meta": doc_meta,
        "total_amount": total_amount,
        "balance": balance,
    }

def scan_sale_product(request):
    code = (request.GET.get("code") or "").strip()
    if not code:
        return JsonResponse({"found": False, "error": "Code requis."}, status=400)
    product_qs = Product.objects.for_scan_code(code)
    product = product_qs.first()
    if not product:
        return JsonResponse({"found": False, "code": code})
    scan = SaleScan.objects.create(
        raw_code=code,
        product=product,
        sale=None,
        scanned_by=request.user if request.user.is_authenticated else None,
    )
    data = {
        "id": product.pk,
        "sku": product.sku,
        "name": product.name,
        "barcode": product.barcode,
        "sale_price": str(product.sale_price or ""),
    }
    return JsonResponse(
        {"found": True, "product": data, "scan_id": scan.pk, "code": code},
        status=200,
    )


def import_products(request):
    site_context = _site_context(request)
    active_site = site_context["active_site"]
    site_locked = bool(active_site and not request.user.is_superuser)
    form = CSVImportForm(
        request.POST or None,
        request.FILES or None,
        current_site=active_site,
        site_locked=site_locked,
    )
    report = None
    if request.method == "POST" and form.is_valid():
        uploaded_file = form.cleaned_data["file"]
        encoding = form.cleaned_data["encoding"]
        apply_quantity = form.cleaned_data["apply_quantity"]
        movement_type = form.cleaned_data["movement_type"]
        import_site = form.cleaned_data["site"]
        if apply_quantity and movement_type is None:
            movement_type = _get_default_entry_movement_type()
        try:
            raw_data = uploaded_file.read().decode(encoding, errors="ignore")
        except UnicodeDecodeError:
            form.add_error("file", "Impossible de décoder ce fichier avec l'encodage sélectionné.")
        else:
            if not raw_data.strip():
                form.add_error("file", "Le fichier semble vide.")
            else:
                try:
                    report = _process_csv_import(
                        raw_data,
                        apply_quantity=apply_quantity,
                        movement_type=movement_type,
                        performed_by=request.user if request.user.is_authenticated else None,
                        site=import_site,
                    )
                except ValueError as exc:
                    form.add_error("file", str(exc))
                else:
                    if report["errors"]:
                        messages.warning(
                            request,
                            f"Import terminé avec des avertissements ({len(report['errors'])} lignes ignorées).",
                        )
                    else:
                        messages.success(
                            request,
                            f"{report['created']} produits créés, {report['updated']} mis à jour.",
                        )
    context = {
        "form": form,
        "report": report,
    }
    context.update(site_context)
    return render(request, "inventory/import_products.html", context)


def export_import_template(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="modele_import_stock.csv"'
    writer = csv.writer(response, delimiter=";")
    writer.writerow(
        [
            "SKU",
            "Ref",
            "Désignation",
            "Description",
            "Marque",
            "Catégorie",
            "Code-barres",
            "Stock minimal",
            "Prix achat",
            "Prix vente",
            "Qté",
            "Unité",
        ]
    )
    writer.writerow(
        [
            "CAM-IP-001",
            "69231725775731",
            "Caméra IP 4MP",
            "Capteur PoE",
            "Dahua",
            "Caméra",
            "69231725775731",
            "5",
            "120.50",
            "199.00",
            "10",
            "PCS",
        ]
    )
    writer.writerow(
        [
            "SW-POE-24P",
            "6939554912345",
            "Switch PoE 24 ports",
            "Rackmount",
            "Ubiquiti",
            "Switch",
            "6939554912345",
            "2",
            "300.00",
            "459.00",
            "5",
            "PCS",
        ]
    )
    return response


def _get_adjustment_movement_type(is_increment):
    code = "AJUSTEMENT_PLUS" if is_increment else "AJUSTEMENT_MOINS"
    movement_type = MovementType.objects.filter(code=code).first()
    if movement_type:
        return movement_type
    direction = MovementType.MovementDirection.ENTRY if is_increment else MovementType.MovementDirection.EXIT
    return MovementType.objects.filter(direction=direction).first()


def lookup_product(request):
    code = request.GET.get("code", "").strip()
    if not code:
        return JsonResponse({"found": False, "error": "Aucun code fourni."}, status=400)
    active_site = _get_active_site(request)
    product = (
        Product.objects.with_stock_quantity(site=active_site)
        .select_related("brand", "category")
        .for_scan_code(code)
        .first()
    )
    if not product:
        return JsonResponse(
            {
                "found": False,
                "created": False,
                "error": "Produit introuvable. Créez-le avant d'enregistrer un mouvement.",
            },
            status=404,
        )
    return JsonResponse(
        {
            "found": True,
            "created": False,
            "product": {
                "id": product.id,
                "name": product.name,
                "sku": product.sku,
                "barcode": product.barcode,
                "brand": product.brand.name,
                "category": product.category.name,
                "stock_quantity": product.stock_quantity,
                "minimum_stock": product.minimum_stock,
            },
        }
    )


def products_feed(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    products = (
        Product.objects.with_stock_quantity()
        .select_related("brand", "category")
        .order_by("name")
    )
    payload = []
    for product in products:
        payload.append(
            {
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "description": product.description,
                "brand": product.brand.name,
                "category": product.category.name,
                "barcode": product.barcode,
                "sale_price": str(product.sale_price) if product.sale_price is not None else None,
                "purchase_price": str(product.purchase_price)
                if product.purchase_price is not None
                else None,
                "stock_quantity": product.current_stock,
                "image_url": product.image.url if product.image else None,
                "updated_at": product.updated_at.isoformat(),
            }
        )
    return JsonResponse({"count": len(payload), "results": payload})


def _create_product_from_scan(code: str) -> Product:
    normalized = (code or "").strip()
    brand = _get_default_brand()
    category = _get_default_category()
    sku = _generate_sku_from_scan(normalized)
    barcode = normalized[:128]
    name = f"Produit {normalized}".strip()[:255] or "Produit sans nom"
    return Product.objects.create(
        sku=sku,
        barcode=barcode or None,
        manufacturer_reference=normalized or None,
        name=name,
        brand=brand,
        category=category,
    )


def _get_default_brand() -> Brand:
    brand, _ = Brand.objects.get_or_create(name="Générique")
    return brand


def _get_default_category() -> Category:
    category, _ = Category.objects.get_or_create(name="Non classé")
    return category


def _get_or_create_brand_by_name(name: str) -> Brand:
    cleaned = (name or "").strip()
    if not cleaned:
        return _get_default_brand()
    brand, _ = Brand.objects.get_or_create(name=cleaned)
    return brand


def _get_or_create_category_by_name(name: str) -> Category:
    cleaned = (name or "").strip()
    if not cleaned:
        return _get_default_category()
    category, _ = Category.objects.get_or_create(name=cleaned)
    return category


def _generate_sale_reference() -> str:
    prefix = timezone.now().strftime("VTE-%Y%m%d")
    counter = 1
    while True:
        candidate = f"{prefix}-{counter:03d}"
        if not Sale.objects.filter(reference=candidate).exists():
            return candidate
        counter += 1


def _generate_sku_from_scan(code: str) -> str:
    base = "".join(ch for ch in code.upper() if ch.isalnum()) or "PROD"
    base = base[:90] if len(base) > 90 else base
    candidate = base
    counter = 1
    while Product.objects.filter(sku__iexact=candidate).exists():
        suffix = f"-{counter}"
        candidate = f"{base[: 100 - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def _get_default_entry_movement_type() -> MovementType | None:
    movement_type = MovementType.objects.filter(
        direction=MovementType.MovementDirection.ENTRY
    ).order_by("id").first()
    return movement_type


def _get_assigned_site(request):
    if not request.user.is_authenticated:
        return None
    assignment = getattr(request.user, "site_assignment", None)
    return assignment.site if assignment else None


def _get_requested_site(request):
    site_id = request.GET.get("site")
    if site_id:
        return Site.objects.filter(pk=site_id).first()
    return None


def _get_action_site(request):
    if not request.user.is_authenticated:
        return None
    assigned_site = _get_assigned_site(request)
    if assigned_site:
        return assigned_site
    if request.user.is_superuser:
        return _get_requested_site(request)
    return None


def _get_active_site(request):
    requested = _get_requested_site(request)
    if requested:
        return requested
    return _get_assigned_site(request)


def _site_context(request):
    sites = list(Site.objects.order_by("name"))
    active_site = _get_active_site(request)
    action_site = _get_action_site(request)
    selected_site_id = request.GET.get("site")
    if not selected_site_id and active_site:
        selected_site_id = str(active_site.pk)
    return {
        "sites": sites,
        "active_site": active_site,
        "action_site": action_site,
        "can_switch_site": True,
        "selected_site": selected_site_id or "",
    }


def _build_site_stock_map(products):
    product_ids = [product.pk for product in products if product.pk]
    if not product_ids:
        return {}
    signed_quantity = Case(
        When(
            movement_type__direction=MovementType.MovementDirection.ENTRY,
            then=F("quantity"),
        ),
        When(
            movement_type__direction=MovementType.MovementDirection.EXIT,
            then=-F("quantity"),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )
    aggregates = (
        StockMovement.objects.filter(product_id__in=product_ids)
        .values("product_id", "site_id", "site__name")
        .annotate(
            quantity=Coalesce(
                Sum(signed_quantity),
                Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("site__name")
    )
    site_stock_map = defaultdict(list)
    for entry in aggregates:
        site_stock_map[entry["product_id"]].append(
            {
                "site_id": entry["site_id"],
                "site_name": entry["site__name"],
                "quantity": entry["quantity"],
            }
        )
    return site_stock_map


def _attach_site_stocks(products):
    site_stock_map = _build_site_stock_map(products)
    for product in products:
        product.site_stocks = site_stock_map.get(product.pk, [])


def _process_csv_import(
    raw_data: str,
    apply_quantity: bool,
    movement_type: MovementType | None,
    performed_by,
    site: Site | None = None,
):
    delimiter = ";" if raw_data.count(";") >= raw_data.count(",") else ","
    reader = csv.DictReader(io.StringIO(raw_data), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("Impossible de détecter les en-têtes du fichier.")
    summary = {
        "created": 0,
        "updated": 0,
        "movements": 0,
        "errors": [],
    }
    brand = _get_default_brand()
    category = _get_default_category()
    movement_site = site or get_default_site()
    if movement_site is None:
        raise ValueError("Aucun site configuré pour les mouvements de stock.")
    with transaction.atomic():
        for index, row in enumerate(reader, start=2):
            normalized = {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in row.items()
            }

            def get_value(*keys):
                for key in keys:
                    key_lower = key.lower()
                    if normalized.get(key_lower):
                        return normalized.get(key_lower)
                return ""

            sku = get_value("sku", "ref", "réf", "reference")
            reference_value = get_value("ref", "réf", "reference")
            if not sku:
                sku = reference_value
            name = get_value("désignation", "designation", "nom", "name")
            quantity_raw = get_value("qté", "qte", "quantite", "qty")

            if not sku or not name:
                summary["errors"].append(f"Ligne {index}: SKU/Ref ou désignation manquante.")
                continue

            manufacturer_reference = get_value(
                "manufacturer_reference", "reference", "ref", "réf"
            ) or sku
            description = get_value("description", "commentaire", "notes")
            barcode = get_value("barcode", "code-barres", "code barre")
            brand_name = get_value("marque", "brand")
            category_name = get_value("catégorie", "categorie", "category")
            min_stock_value = _parse_int(get_value("stock minimal", "stock_min", "minimum_stock"))
            purchase_price_value = _parse_decimal(get_value("prix achat", "purchase_price"))
            sale_price_value = _parse_decimal(get_value("prix vente", "sale_price"))

            brand = _get_or_create_brand_by_name(brand_name) if brand_name else _get_default_brand()
            category = (
                _get_or_create_category_by_name(category_name)
                if category_name
                else _get_default_category()
            )

            defaults = {
                "name": name,
                "manufacturer_reference": manufacturer_reference,
                "barcode": barcode or manufacturer_reference,
                "brand": brand,
                "category": category,
                "description": description,
                "minimum_stock": min_stock_value or 0,
                "purchase_price": purchase_price_value,
                "sale_price": sale_price_value,
            }
            product, created = Product.objects.get_or_create(
                sku=sku,
                defaults=defaults,
            )
            if created:
                summary["created"] += 1
            else:
                updated_fields = []
                if manufacturer_reference and product.manufacturer_reference != manufacturer_reference:
                    product.manufacturer_reference = manufacturer_reference
                    updated_fields.append("manufacturer_reference")
                if product.name != name:
                    product.name = name
                    updated_fields.append("name")
                if barcode and product.barcode != barcode:
                    product.barcode = barcode
                    updated_fields.append("barcode")
                if description and product.description != description:
                    product.description = description
                    updated_fields.append("description")
                if brand_name and product.brand != brand:
                    product.brand = brand
                    updated_fields.append("brand")
                if category_name and product.category != category:
                    product.category = category
                    updated_fields.append("category")
                if min_stock_value is not None and product.minimum_stock != min_stock_value:
                    product.minimum_stock = min_stock_value
                    updated_fields.append("minimum_stock")
                if purchase_price_value is not None and product.purchase_price != purchase_price_value:
                    product.purchase_price = purchase_price_value
                    updated_fields.append("purchase_price")
                if sale_price_value is not None and product.sale_price != sale_price_value:
                    product.sale_price = sale_price_value
                    updated_fields.append("sale_price")
                if updated_fields:
                    product.save(update_fields=updated_fields + ["updated_at"])
                    summary["updated"] += 1

            if (
                apply_quantity
                and movement_type
                and quantity_raw
            ):
                quantity_value = _parse_int(quantity_raw)
                if quantity_value is None:
                    summary["errors"].append(
                        f"Ligne {index}: quantité invalide '{quantity_raw}'."
                    )
                    continue
                if quantity_value > 0:
                    StockMovement.objects.create(
                        product=product,
                        movement_type=movement_type,
                        quantity=quantity_value,
                        movement_date=timezone.now(),
                        performed_by=performed_by,
                        comment="Import CSV",
                        site=movement_site,
                    )
                    summary["movements"] += 1
    return summary


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = value.replace(" ", "").replace(",", ".")
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    cleaned = value.replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
