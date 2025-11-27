from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.management.commands.import_render_products import (
    _as_decimal,
    _build_image_filename,
    _build_sku,
    _category_name,
    _clean_description,
    _clean_text,
    _compute_barcode,
    _ensure_brand,
    _ensure_category,
    _ensure_unique_barcode,
)
from inventory.models import Product


class Command(BaseCommand):
    help = "Import only new products from a JSON export without touching existing ones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            "-f",
            type=str,
            default="products_site_transformed.json",
            help="Path to the JSON file containing the merged product list.",
        )
        parser.add_argument(
            "--skip-images",
            action="store_true",
            help="Do not download product images.",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"]).expanduser()
        if not file_path.exists():
            raise CommandError(f"Le fichier JSON indique est introuvable: {file_path}")

        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)

        raw_content = file_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Impossible de parser le fichier JSON: {exc}") from exc

        if not isinstance(payload, list):
            raise CommandError("Le fichier JSON doit contenir une liste de produits.")

        existing_skus = set(Product.objects.values_list("sku", flat=True))
        summary = {
            "created": 0,
            "existing": 0,
            "images_downloaded": 0,
            "image_errors": 0,
            "errors": [],
        }

        for record in payload:
            sku = _build_sku(record)
            if sku in existing_skus:
                summary["existing"] += 1
                continue
            try:
                with transaction.atomic():
                    product = self._create_product(record, sku)
                summary["created"] += 1
                existing_skus.add(sku)
                if not options["skip_images"]:
                    downloaded = self._download_image(product, record)
                    if downloaded:
                        summary["images_downloaded"] += 1
                    elif record.get("image_1920") or record.get("image_url"):
                        summary["image_errors"] += 1
            except Exception as exc:  # pylint: disable=broad-except
                summary["errors"].append(f"{sku}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                "Import termine -> nouveaux produits: %(created)d, deja presents: %(existing)d, "
                "images telechargees: %(images_downloaded)d, images en erreur: %(image_errors)d."
                % summary
            )
        )
        if summary["errors"]:
            self.stdout.write(self.style.ERROR("Erreurs rencontrees:"))
            for error in summary["errors"]:
                self.stdout.write(f"- {error}")

    def _create_product(self, record: dict, sku: str) -> Product:
        brand = _ensure_brand(record.get("brand"))
        category = _ensure_category(_category_name(record))
        name = _clean_text(record.get("name"))
        if not name:
            identifier = record.get("odoo_id") or record.get("id")
            name = f"Produit {identifier or sku}"
        manufacturer_reference = (
            _clean_text(record.get("default_code"))
            or _clean_text(record.get("slug"))
            or str(record.get("odoo_id") or record.get("id") or sku)
        )[:100]
        description = _clean_description(record.get("description")) or _clean_description(
            record.get("short_description")
        )
        raw_barcode = _compute_barcode(record)
        barcode = _ensure_unique_barcode(raw_barcode, sku)
        sale_price = _as_decimal(record.get("list_price"))

        product = Product.objects.create(
            sku=sku,
            name=name,
            manufacturer_reference=manufacturer_reference,
            description=description,
            brand=brand,
            category=category,
            barcode=barcode,
            sale_price=sale_price,
        )
        return product

    def _download_image(self, product: Product, record: dict) -> bool:
        image_url = record.get("image_1920") or record.get("image_url")
        if not image_url:
            return False
        parsed = urlparse(image_url)
        source_name = Path(parsed.path).name or "image.jpg"
        filename = _build_image_filename(product.sku, source_name)
        try:
            response = requests.get(image_url, timeout=20)
            response.raise_for_status()
        except Exception:
            return False
        if not response.content:
            return False
        product.image.save(filename, ContentFile(response.content), save=True)
        return True
