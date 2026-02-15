from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.bot import MistralTextGenerator
from inventory.models import Category, Product, SubCategory

DEFAULT_SOURCE_URL = "https://samr.pythonanywhere.com/api/products/"


@dataclass
class CategorySuggestion:
    category: str
    subcategory: str | None = None


def _clean(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def _parse_response(raw: str) -> CategorySuggestion | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        text = match.group(0)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    category = _clean(payload.get("category"))
    subcategory = _clean(payload.get("subcategory"))
    if not category or category.lower() in {"none", "null", "n/a"}:
        return None
    if subcategory.lower() in {"none", "null", "n/a", ""}:
        subcategory = None
    return CategorySuggestion(category=category, subcategory=subcategory)


def _build_prompt(product: dict, categories: list[Category], max_subcategories: int = 100) -> str:
    category_lines = []
    for category in categories:
        subs = list(category.subcategories.order_by("name").values_list("name", flat=True)[:max_subcategories])
        if subs:
            category_lines.append(f"- {category.name} -> {', '.join(subs)}")
        else:
            category_lines.append(f"- {category.name}")
    details = [
        f"Nom: {_clean(product.get('name'))}",
        f"SKU: {_clean(product.get('sku'))}",
        f"Marque: {_clean(product.get('brand'))}",
        f"Reference: {_clean(product.get('manufacturer_reference'))}",
        f"Description: {_clean(product.get('description'))[:280]}",
    ]
    return (
        "Tu classifies des produits de securite electronique. "
        "Retourne exactement un JSON sur une ligne: "
        '{"category":"...","subcategory":"..."}. '\
        "Tu peux creer une nouvelle categorie ou sous-categorie si necessaire. "
        "Si la sous-categorie n'est pas utile, mets null.\n\n"
        + "\n".join(details)
        + "\n\nCategories actuelles:\n"
        + ("\n".join(category_lines) if category_lines else "(aucune)")
    )


class Command(BaseCommand):
    help = "Synchronise categories/sous-categories depuis une API produits distante avec Mistral."

    def add_arguments(self, parser):
        parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--timeout", type=int, default=25)
        parser.add_argument("--no-ai", action="store_true", help="Utilise uniquement category/subcategory du flux JSON.")

    def handle(self, *args, **options):
        source_url = options["source_url"]
        timeout = max(5, int(options["timeout"]))
        use_ai = not options["no_ai"]

        response = requests.get(source_url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise CommandError("Le flux distant doit etre une liste ou un objet avec 'results'.")
        if options["limit"]:
            items = items[: int(options["limit"])]

        generator = None
        if use_ai:
            api_key = getattr(settings, "MISTRAL_API_KEY", None)
            if not api_key:
                raise CommandError("MISTRAL_API_KEY est requis (ou lance avec --no-ai).")
            generator = MistralTextGenerator(
                api_key=api_key,
                model=getattr(settings, "MISTRAL_MODEL", "mistral-medium-latest"),
                agent_id=getattr(settings, "MISTRAL_AGENT_ID", None),
            )

        summary = {
            "processed": 0,
            "categories_created": 0,
            "subcategories_created": 0,
            "products_updated": 0,
            "errors": 0,
        }

        for product_data in items:
            summary["processed"] += 1
            try:
                self._process_product(product_data, generator, options["dry_run"], summary)
            except Exception as exc:  # noqa: BLE001
                summary["errors"] += 1
                self.stdout.write(self.style.WARNING(f"Produit ignore ({product_data.get('sku')}): {exc}"))

        self.stdout.write(
            self.style.SUCCESS(
                "Sync categories terminee - "
                f"produits: {summary['processed']}, categories creees: {summary['categories_created']}, "
                f"sous-categories creees: {summary['subcategories_created']}, "
                f"produits maj: {summary['products_updated']}, erreurs: {summary['errors']}."
            )
        )

    def _process_product(self, product_data: dict, generator, dry_run: bool, summary: dict) -> None:
        category_name = _clean(product_data.get("category"))
        subcategory_name = _clean(product_data.get("subcategory"))

        if generator:
            prompt = _build_prompt(product_data, list(Category.objects.prefetch_related("subcategories").all()))
            answer = generator.generate_text(prompt, temperature=0.1, max_tokens=140)
            suggestion = _parse_response(answer or "")
            if suggestion:
                category_name = suggestion.category
                subcategory_name = suggestion.subcategory or ""

        if not category_name:
            return

        with transaction.atomic():
            category, created_category = Category.objects.get_or_create(name=category_name)
            if created_category:
                summary["categories_created"] += 1

            subcategory = None
            if subcategory_name:
                subcategory, created_sub = SubCategory.objects.get_or_create(
                    category=category,
                    name=subcategory_name,
                )
                if created_sub:
                    summary["subcategories_created"] += 1

            sku = _clean(product_data.get("sku"))
            if not sku or dry_run:
                return

            product = Product.objects.filter(sku=sku).first()
            if not product:
                return
            updates = []
            if product.category_id != category.id:
                product.category = category
                updates.append("category")
            if (product.subcategory_id or None) != (subcategory.id if subcategory else None):
                product.subcategory = subcategory
                updates.append("subcategory")
            if updates:
                product.save(update_fields=updates)
                summary["products_updated"] += 1
