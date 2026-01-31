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
    asset_changes: dict | None = None,
    log_level: str = ProductAssetJobLog.Level.INFO,
) -> None:
    now = timezone.now()
    job.status = ProductAssetJob.Status.SUCCESS if success else ProductAssetJob.Status.FAILED
    job.finished_at = now
    job.processed_products = 1
    job.last_message = message
    job.description_changed = description_changed
    job.image_changed = image_changed
    job.asset_changes = asset_changes or {}
    job.save(
        update_fields=[
            "status",
            "finished_at",
            "last_message",
            "processed_products",
            "description_changed",
            "image_changed",
            "asset_changes",
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
    assets: list[str],
    force_description: bool,
    force_image: bool,
    force_techsheet: bool,
    force_pdf: bool,
    force_videos: bool,
    force_blog: bool,
) -> ProductAssetJob:
    job = ProductAssetJob.objects.create(
        product=product,
        mode=mode,
        total_products=1,
        processed_products=0,
        assets=assets,
        force_description=force_description,
        force_image=force_image,
        force_techsheet=force_techsheet,
        force_pdf=force_pdf,
        force_videos=force_videos,
        force_blog=force_blog,
        status=ProductAssetJob.Status.QUEUED,
        last_message="En file d'attente.",
    )
    _log_job(job, "Produit en file d'attente.")
    return job


def reserve_product_asset_job(
    product: Product,
    mode: str,
    assets: list[str],
    force_description: bool,
    force_image: bool,
    force_techsheet: bool,
    force_pdf: bool,
    force_videos: bool,
    force_blog: bool,
) -> Tuple[ProductAssetJob, bool]:
    pending = get_pending_product_asset_job(product)
    if pending:
        updated = False
        if assets and pending.assets != assets:
            pending.assets = assets
            updated = True
        if force_description and not pending.force_description:
            pending.force_description = True
            updated = True
        if force_image and not pending.force_image:
            pending.force_image = True
            updated = True
        if force_techsheet and not pending.force_techsheet:
            pending.force_techsheet = True
            updated = True
        if force_pdf and not pending.force_pdf:
            pending.force_pdf = True
            updated = True
        if force_videos and not pending.force_videos:
            pending.force_videos = True
            updated = True
        if force_blog and not pending.force_blog:
            pending.force_blog = True
            updated = True
        if updated:
            pending.last_message = "Paramètres de traitement mis à jour."
            pending.save(
                update_fields=[
                    "assets",
                    "force_description",
                    "force_image",
                    "force_techsheet",
                    "force_pdf",
                    "force_videos",
                    "force_blog",
                    "last_message",
                    "updated_at",
                ]
            )
            _log_job(pending, "Options de traitement mises à jour.")
        return pending, False
    return (
        create_product_asset_job(
            product,
            mode,
            assets,
            force_description,
            force_image,
            force_techsheet,
            force_pdf,
            force_videos,
            force_blog,
        ),
        True,
    )


def run_product_asset_bot(
    product_id: int,
    force_description: bool = False,
    force_image: bool = False,
    force_techsheet: bool = False,
    force_pdf: bool = False,
    force_videos: bool = False,
    force_blog: bool = False,
    assets: list[str] | None = None,
    *,
    job_id: int | None = None,
    preview_image: bool = False,
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
    image_field = "pending_image" if preview_image else "image"
    changes = bot.ensure_assets(
        product,
        assets=assets,
        force_description=force_description,
        force_image=force_image,
        force_techsheet=force_techsheet,
        force_pdf=force_pdf,
        force_videos=force_videos,
        force_blog=force_blog,
        image_field=image_field,
    )
    description_changed = changes.get("description_changed", False)
    image_changed = changes.get("image_changed", False)
    image_log = getattr(bot, "last_image_log", None)
    if image_log:
        logger.info("Product asset bot image log for %s: %s", product.sku, image_log)
        if job:
            _log_job(job, image_log)

    update_fields: list[str] = []
    if changes.get("short_description_changed"):
        update_fields.append("short_description")
    if changes.get("long_description_changed"):
        update_fields.append("long_description")
    if description_changed:
        update_fields.append("description")
    if changes.get("tech_specs_changed"):
        update_fields.append("tech_specs_json")
    if changes.get("videos_changed"):
        update_fields.append("video_links")
    if image_changed:
        update_fields.append(image_field)
        placeholder_field = (
            "pending_image_is_placeholder" if image_field == "pending_image" else "image_is_placeholder"
        )
        update_fields.append(placeholder_field)

    if update_fields:
        update_fields = sorted({field for field in update_fields})
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
    tech_flag = "specs" if changes.get("tech_specs_changed") else "skip-specs"
    pdf_flag = "pdf" if changes.get("pdf_changed") else "skip-pdf"
    video_flag = "video" if changes.get("videos_changed") else "skip-video"
    blog_flag = "blog" if changes.get("blog_changed") else "skip-blog"
    message = (
        f"{product.sku} ({product.name}) {desc_flag} {image_flag} "
        f"{tech_flag} {pdf_flag} {video_flag} {blog_flag}"
    )
    if image_log:
        message = f"{message} | {image_log}"

    if job:
        _finalize_job(
            job,
            success=success,
            message=message,
            description_changed=description_changed,
            image_changed=image_changed,
            asset_changes=changes,
        )

    return {
        "product_id": product_id,
        "sku": product.sku,
        "description_changed": description_changed,
        "image_changed": image_changed,
        "short_description_changed": changes.get("short_description_changed", False),
        "long_description_changed": changes.get("long_description_changed", False),
        "tech_specs_changed": changes.get("tech_specs_changed", False),
        "pdf_changed": changes.get("pdf_changed", False),
        "videos_changed": changes.get("videos_changed", False),
        "blog_changed": changes.get("blog_changed", False),
        "job_id": job.pk if job else None,
        "image_preview": preview_image,
    }
