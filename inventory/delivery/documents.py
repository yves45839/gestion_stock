"""Generate private attachment snapshots using the existing document templates."""
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from inventory.models import Sale, Site


class DocumentError(Exception):
    pass


def allowed_site(request):
    if not request.user.is_superuser:
        assignment = getattr(request.user, "site_assignment", None)
        if not assignment:
            raise PermissionDenied("Votre compte doit être affecté à un site pour préparer un envoi.")
        requested = request.GET.get("site")
        if requested and requested != str(assignment.site_id):
            raise PermissionDenied("Ce rapport appartient à un autre site.")
        return assignment.site
    site_id = request.GET.get("site", request.session.get("active_site_id"))
    if not site_id:
        return None
    try:
        return Site.objects.get(pk=site_id)
    except (Site.DoesNotExist, ValueError, TypeError):
        raise Http404("Site inconnu.")


def check_sale(request, sale):
    site = allowed_site(request)
    if not request.user.is_superuser and sale.site_id != site.pk:
        raise PermissionDenied("Ce document appartient à un autre site.")


def report_snapshot(request):
    from inventory.views import _resolve_period_range, PERIOD_LABELS
    site = allowed_site(request)
    period, start, end, _ = _resolve_period_range(
        request.GET.get("period") or "month", timezone.now(),
        request.GET.get("start"), request.GET.get("end"),
    )
    # Freeze the resolved dates, including relative periods, before confirmation.
    return site, {"period": period, "start": start.isoformat(), "end": end.isoformat(),
                  "label": PERIOD_LABELS.get(period, period)}


def render_pdf(request, batch):
    from inventory.views import HTML, _build_document_context, _get_document_meta
    if HTML is None:
        raise DocumentError("Le moteur PDF WeasyPrint doit être installé pour préparer le document.")
    if batch.document_type == "report":
        from datetime import datetime
        filters = batch.report_filters
        sales = Sale.objects.filter(status=Sale.Status.CONFIRMED,
            sale_date__gte=datetime.fromisoformat(filters["start"]),
            sale_date__lte=datetime.fromisoformat(filters["end"])).select_related("customer").prefetch_related("items__product").order_by("-sale_date")
        if batch.site_id:
            sales = sales.filter(site_id=batch.site_id)
        context = {"confirmed_sales": sales, "period_label": filters["label"],
                   "range_label": f"{datetime.fromisoformat(filters['start']):%d/%m/%Y} – {datetime.fromisoformat(filters['end']):%d/%m/%Y}",
                   "generated_at": batch.created_at, "delivery_report": True, "report_site": batch.site}
        template = "inventory/analytics_confirmed_sales_pdf.html"
    else:
        meta = _get_document_meta(batch.sale, batch.document_type)
        context = _build_document_context(batch.sale, meta) | {"pdf_export": True}
        template = "inventory/document_preview.html"
    context["delivery_demo"] = getattr(settings, "IS_SALES_DEMO", False)
    html = render_to_string(template, context, request=request)
    # No remote media or user-controlled URLs are needed by these templates.
    def local_assets_only(url, *args, **kwargs):
        if url.startswith("data:"):
            from weasyprint import default_url_fetcher
            return default_url_fetcher(url, *args, **kwargs)
        raise ValueError("External resources are disabled for delivery documents")
    return HTML(string=html, url_fetcher=local_assets_only).write_pdf()


def batch_folder(batch):
    return Path(settings.DOCUMENT_DELIVERY_ROOT) / str(batch.pk)


def prepare_attachments(request, batch):
    pdf = render_pdf(request, batch)
    stem = slugify(batch.title)[:100] or "document"
    attachments = []
    if batch.file_format == "pdf":
        files = [(stem + ".pdf", "application/pdf", pdf)]
    else:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            raise DocumentError("Installez pypdfium2 pour les images de rapport.")
        files = []
        document = pdfium.PdfDocument(pdf)
        try:
            if len(document) > 20:
                raise DocumentError("Ce rapport dépasse 20 pages. Réduisez la période ou choisissez le PDF.")
            for index in range(len(document)):
                page = document[index]
                bitmap = page.render(scale=1.5)
                image = bitmap.to_pil()
                stream = BytesIO()
                image.save(stream, format="PNG")
                files.append((f"{stem}-page-{index + 1:02}.png", "image/png", stream.getvalue()))
                image.close()
                bitmap.close()
                page.close()
        finally:
            document.close()
    if any(len(data) > 5 * 1024 * 1024 for _, _, data in files) or sum(len(data) for _, _, data in files) > 15 * 1024 * 1024:
        raise DocumentError("Les pièces jointes sont trop volumineuses. Réduisez la période du rapport.")
    folder = batch_folder(batch)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, mimetype, content in files:
        (folder / name).write_bytes(content)
        attachments.append({"name": name, "mimetype": mimetype, "size": len(content)})
    return attachments
