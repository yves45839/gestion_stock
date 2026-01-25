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
        queryset = Product.objects.all().order_by("name")
        if not options["force_description"]:
            queryset = queryset.filter(Q(description="") | Q(description__isnull=True))
        if not options["force_image"]:
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
                force_description=options["force_description"],
                force_image=options["force_image"],
            )
            if not created:
                self.stdout.write(f"{product.sku} est déjà en file d'attente.")
                continue
            if inline_mode:
                run_product_asset_bot(
                    product.pk,
                    force_description=options["force_description"],
                    force_image=options["force_image"],
                    job_id=job.pk,
                )
                self.stdout.write(f"Processed bot inline for {product.sku} ({product.name})")
            else:
                enqueue_product_asset_job(
                    job.pk,
                    [product.pk],
                    force_description=options["force_description"],
                    force_image=options["force_image"],
                )
                self.stdout.write(f"Queued bot for {product.sku} ({product.name})")
