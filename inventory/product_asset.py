import logging
from typing import Tuple

from django.utils import timezone

from .bot import ProductAssetBot
from .models import Product, ProductAssetJob, ProductAssetJobLog

logger = logging.getLogger(__name__)


def _log_job(job: ProductAssetJob, message: str, level: str = ProductAssetJobLog.Level.INFO) -> None:
    ProductAssetJobLog.objects.create(job=job, message=message, level=level)


def _start_job(job: ProductAssetJob) -> None:
    if job.status == ProductAssetJob.Status.RUNNING:
        return
    now = timezone.now()
    job.status = ProductAssetJob.Status.RUNNING
    job.started_at = job.started_at or now
    job.last_message = "En cours..."
    job.save(update_fields=["status", "started_at", "last_message"])


def _finalize_job(
    job: ProductAssetJob,
    *,
    success: bool,
    message: str,
    description_changed: bool,
    image_changed: bool,
    log_level: str = ProductAssetJobLog.Level.INFO,
) -> None:
    now = timezone.now()
    job.status = ProductAssetJob.Status.SUCCESS if success else ProductAssetJob.Status.FAILED
    job.finished_at = now
    job.processed_products = 1
    job.last_message = message
    job.description_changed = description_changed
    job.image_changed = image_changed
    job.save(
        update_fields=[
            "status",
            "finished_at",
            "last_message",
            "processed_products",
            "description_changed",
            "image_changed",
        ]
    )
    _log_job(job, message, level=log_level)


def get_pending_product_asset_job(product: Product) -> ProductAssetJob | None:
    return ProductAssetJob.objects.filter(
        product=product,
        status__in=(ProductAssetJob.Status.QUEUED, ProductAssetJob.Status.RUNNING),
    ).first()


def create_product_asset_job(
    product: Product,
    mode: str,
    force_description: bool,
    force_image: bool,
) -> ProductAssetJob:
    job = ProductAssetJob.objects.create(
        product=product,
        mode=mode,
        total_products=1,
        processed_products=0,
        force_description=force_description,
        force_image=force_image,
        status=ProductAssetJob.Status.QUEUED,
        last_message="En file d'attente.",
    )
    _log_job(job, "Produit en file d'attente.")
    return job


def reserve_product_asset_job(
    product: Product,
    mode: str,
    force_description: bool,
    force_image: bool,
) -> Tuple[ProductAssetJob, bool]:
    pending = get_pending_product_asset_job(product)
    if pending:
        updated = False
        if force_description and not pending.force_description:
            pending.force_description = True
            updated = True
        if force_image and not pending.force_image:
            pending.force_image = True
            updated = True
        if updated:
            pending.last_message = "Paramètres de traitement mis à jour."
            pending.save(
                update_fields=[
                    "force_description",
                    "force_image",
                    "last_message",
                    "updated_at",
                ]
            )
            _log_job(pending, "Options de traitement mises à jour.")
        return pending, False
    return create_product_asset_job(product, mode, force_description, force_image), True


def run_product_asset_bot(
    product_id: int,
    force_description: bool = False,
    force_image: bool = False,
    *,
    job_id: int | None = None,
) -> dict[str, bool | int | str]:
    job = (
        ProductAssetJob.objects.filter(pk=job_id).first() if job_id is not None else None
    )
    product = (
        Product.objects.select_related("brand", "category")
        .filter(pk=product_id)
        .first()
    )
    if not product:
        logger.warning("Product asset bot: product %s was not found.", product_id)
        if job:
            message = f"Produit {product_id} introuvable."
            _finalize_job(
                job,
                success=False,
                message=message,
                description_changed=False,
                image_changed=False,
                log_level=ProductAssetJobLog.Level.WARNING,
            )
        return {"product_id": product_id, "status": "missing"}

    if job:
        _start_job(job)

    bot = ProductAssetBot()
    description_changed, image_changed = bot.ensure_assets(
        product, force_description=force_description, force_image=force_image
    )
    image_log = getattr(bot, "last_image_log", None)
    if image_log:
        logger.info("Product asset bot image log for %s: %s", product.sku, image_log)
        if job:
            _log_job(job, image_log)

    if description_changed or image_changed:
        update_fields = []
        if description_changed:
            update_fields.append("description")
        if image_changed:
            update_fields.append("image")
        product.save(update_fields=update_fields)
        logger.info(
            "Product asset bot updated %s (desc=%s image=%s)",
            product.sku,
            description_changed,
            image_changed,
        )
        success = True
    else:
        logger.info("Product asset bot had nothing to update for %s", product.sku)
        success = True

    desc_flag = "desc" if description_changed else "skip-desc"
    image_flag = "img" if image_changed else "skip-img"
    message = f"{product.sku} ({product.name}) {desc_flag} {image_flag}"
    if image_log:
        message = f"{message} | {image_log}"

    if job:
        _finalize_job(
            job,
            success=success,
            message=message,
            description_changed=description_changed,
            image_changed=image_changed,
        )

    return {
        "product_id": product_id,
        "sku": product.sku,
        "description_changed": description_changed,
        "image_changed": image_changed,
        "job_id": job.pk if job else None,
    }
