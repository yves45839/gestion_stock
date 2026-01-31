from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from inventory.background import enqueue_product_asset_job
from inventory.models import Product, ProductAssetJob
from inventory.product_asset import (
    reserve_product_asset_job,
    run_product_asset_bot,
)


class Command(BaseCommand):
    help = "Queue the AI product asset bot to enrich descriptions and images."

    def add_arguments(self, parser):
        parser.add_argument(
            "--assets",
            type=str,
            help="Comma-separated list of assets to generate (description,images,techsheet,pdf,videos,blog).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of products to enqueue (applied after filtering).",
        )
        parser.add_argument(
            "--force-description",
            action="store_true",
            help="Regenerate the description even if one already exists.",
        )
        parser.add_argument(
            "--force-image",
            action="store_true",
            help="Replace existing images with AI placeholders.",
        )
        parser.add_argument(
            "--force-techsheet",
            action="store_true",
            help="Regenerate technical specs even if they exist.",
        )
        parser.add_argument(
            "--force-pdf",
            action="store_true",
            help="Regenerate PDF brochure summaries even if they exist.",
        )
        parser.add_argument(
            "--force-videos",
            action="store_true",
            help="Regenerate video links even if they exist.",
        )
        parser.add_argument(
            "--force-blog",
            action="store_true",
            help="Regenerate blog content even if it exists.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which products would be queued without enqueueing.",
        )
        parser.add_argument(
            "--inline",
            action="store_true",
            help="Process matching products right away instead of enqueueing a Celery task.",
        )

    def handle(self, *args, **options):
        assets = _normalize_assets(options.get("assets"))
        queryset = Product.objects.all().order_by("name")
        if "description" in assets and not options["force_description"]:
            queryset = queryset.filter(Q(description="") | Q(description__isnull=True))
        if "images" in assets and not options["force_image"]:
            queryset = queryset.filter(Q(image="") | Q(image__isnull=True))

        products = list(queryset)
        limit = options.get("limit")
        if limit:
            products = products[:limit]

        inline_mode = options["inline"] or settings.PRODUCT_BOT_INLINE_RUN
        if not products:
            self.stdout.write("No products matched the criteria.")
            return

        for product in products:
            if options["dry_run"]:
                verb = "queue" if not inline_mode else "process"
                self.stdout.write(f"Would {verb} bot for {product.sku} ({product.name})")
                continue
            job, created = reserve_product_asset_job(
                product,
                mode=ProductAssetJob.Mode.BATCH,
                assets=assets,
                force_description=options["force_description"],
                force_image=options["force_image"],
                force_techsheet=options["force_techsheet"],
                force_pdf=options["force_pdf"],
                force_videos=options["force_videos"],
                force_blog=options["force_blog"],
            )
            if not created:
                self.stdout.write(f"{product.sku} est déjà en file d'attente.")
                continue
            if inline_mode:
                run_product_asset_bot(
                    product.pk,
                    assets=assets,
                    force_description=options["force_description"],
                    force_image=options["force_image"],
                    force_techsheet=options["force_techsheet"],
                    force_pdf=options["force_pdf"],
                    force_videos=options["force_videos"],
                    force_blog=options["force_blog"],
                    job_id=job.pk,
                )
                self.stdout.write(f"Processed bot inline for {product.sku} ({product.name})")
            else:
                enqueue_product_asset_job(
                    job.pk,
                    [product.pk],
                    assets=assets,
                    force_description=options["force_description"],
                    force_image=options["force_image"],
                    force_techsheet=options["force_techsheet"],
                    force_pdf=options["force_pdf"],
                    force_videos=options["force_videos"],
                    force_blog=options["force_blog"],
                )
                self.stdout.write(f"Queued bot for {product.sku} ({product.name})")


def _normalize_assets(raw: str | None) -> list[str]:
    if not raw:
        return ["description", "images"]
    assets = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return assets or ["description", "images"]
