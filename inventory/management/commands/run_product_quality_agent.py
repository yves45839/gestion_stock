from django.core.management.base import BaseCommand

from inventory.models import Product
from inventory.quality_agent import ProductQualityAgent


class Command(BaseCommand):
    help = "Audit product quality and auto-improve low-scoring products with AI enrichment."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", type=int, help="Audit only one product id")
        parser.add_argument("--threshold", type=int, default=70, help="Minimum quality score")
        parser.add_argument("--limit", type=int, default=50, help="Max products to process")

    def handle(self, *args, **options):
        threshold = int(options["threshold"])
        limit = int(options["limit"])
        product_id = options.get("product_id")

        queryset = Product.objects.select_related("brand", "category").order_by("id")
        if product_id:
            queryset = queryset.filter(id=product_id)
        products = list(queryset[:limit])

        if not products:
            self.stdout.write(self.style.WARNING("Aucun produit trouvé."))
            return

        agent = ProductQualityAgent(threshold=threshold)
        changed_count = 0

        for product in products:
            result = agent.improve_if_needed(product)
            line = (
                f"[{result['status']}] {result['sku']} "
                f"score={result['score']}"
                + (f"->{result['score_after']}" if "score_after" in result else "")
            )
            self.stdout.write(line)
            if result["changed"]:
                changed_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Traitement terminé: {len(products)} produit(s), {changed_count} amélioré(s)."
            )
        )
