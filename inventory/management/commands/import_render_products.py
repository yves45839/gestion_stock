from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Brand,
    Category,
    MovementType,
    Product,
    StockMovement,
    get_default_site,
)


DEFAULT_BRAND_NAME = "Générique"
DEFAULT_CATEGORY_NAME = "Non classé"
SKU_MAX_LENGTH = 100
IMPORT_ENTRY_CODE = "IMPORT_RENDER_IN"
IMPORT_EXIT_CODE = "IMPORT_RENDER_OUT"


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).split())


def _clean_description(value: str | None) -> str:
    cleaned = _clean_text(value)
    normalized = cleaned.lower()
    if not cleaned or normalized in {"false", "none", "null"}:
        return ""
    return cleaned


def _sanitize_sku_segment(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_-]+", "-", value)
    cleaned = cleaned.strip("-_")
    return cleaned or "PROD"


def _build_sku(record: dict) -> str:
    identifier = record.get("odoo_id") or record.get("id")
    suffix = f"-{identifier}" if identifier else ""
    candidates = [
        record.get("default_code"),
        record.get("slug"),
        record.get("name"),
    ]
    base_text = next((c for c in candidates if c), "PROD")
    base_segment = _sanitize_sku_segment(str(base_text))
    if suffix:
        max_base_len = SKU_MAX_LENGTH - len(suffix)
        max_base_len = max(max_base_len, 1)
        base_segment = base_segment[:max_base_len]
        return f"{base_segment}{suffix}"
    return base_segment[:SKU_MAX_LENGTH]


def _as_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


def _ensure_brand(name: str | None) -> Brand:
    cleaned = _clean_text(name)
    if not cleaned:
        cleaned = DEFAULT_BRAND_NAME
    brand, _ = Brand.objects.get_or_create(name=cleaned)
    return brand


def _ensure_category(name: str | None) -> Category:
    cleaned = _clean_text(name)
    if not cleaned:
        cleaned = DEFAULT_CATEGORY_NAME
    category, _ = Category.objects.get_or_create(name=cleaned)
    return category


def _ensure_subcategory(category: Category, name: str | None):
    from inventory.models import SubCategory

    cleaned = _clean_text(name)
    if not cleaned:
        return None
    subcategory, _ = SubCategory.objects.get_or_create(category=category, name=cleaned)
    return subcategory


def _category_main_name(record: dict) -> str | None:
    for key in ("category_main", "categ_id", "category_type"):
        value = record.get(key)
        if value:
            return value
    return record.get("category_sub")


def _category_sub_name(record: dict) -> str | None:
    return record.get("category_sub")


def _compute_barcode(record: dict) -> str | None:
    raw = record.get("barcode") or ""
    cleaned = _clean_text(raw)
    normalized = cleaned.lower()
    if not cleaned or normalized in {"false", "none", "null"}:
        return None
    return cleaned


def _ensure_unique_barcode(barcode: str | None, sku: str) -> str | None:
    if not barcode:
        return None
    conflict = Product.objects.filter(barcode=barcode).exclude(sku=sku).exists()
    return None if conflict else barcode


def _resolve_image_path(images_root: Path, relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    candidate_path = images_root / candidate
    if candidate_path.exists():
        return candidate_path
    return None


def _build_image_filename(sku: str, source_name: str) -> str:
    cleaned_name = re.sub(r"[^0-9A-Za-z._-]+", "_", source_name)
    cleaned_name = cleaned_name.strip("_") or source_name
    base_name = f"{sku}_{cleaned_name}"
    return base_name[:200]


class Command(BaseCommand):
    help = "Import products and stock data exported from the Render API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            "-f",
            type=str,
            default="products_local.json",
            help="Path to the JSON file exported from Render.",
        )
        parser.add_argument(
            "--images-root",
            "-i",
            type=str,
            help="Local root folder that prefixes the 'local_image' values (defaults to the JSON parent).",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"]).expanduser()
        if not file_path.exists():
            raise CommandError(f"Le fichier JSON indiqué est introuvable: {file_path}")
        images_root = (
            Path(options["images_root"]).expanduser()
            if options["images_root"]
            else file_path.parent
        )
        if not images_root.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Le dossier des images ({images_root}) est introuvable. "
                    "Les chemins local_image seront ignorés."
                )
            )
        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)

        movement_site = get_default_site()
        if movement_site is None:
            raise CommandError("Aucun site configuré pour les mouvements d'import.")

        entry_type, _ = MovementType.objects.get_or_create(
            code=IMPORT_ENTRY_CODE,
            defaults={
                "name": "Import Render (entrée)",
                "direction": MovementType.MovementDirection.ENTRY,
            },
        )
        exit_type, _ = MovementType.objects.get_or_create(
            code=IMPORT_EXIT_CODE,
            defaults={
                "name": "Import Render (sortie)",
                "direction": MovementType.MovementDirection.EXIT,
            },
        )

        raw_content = file_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Impossible de parser le fichier JSON: {exc}") from exc

        summary = {
            "created": 0,
            "updated": 0,
            "stock_movements": 0,
            "images_saved": 0,
            "missing_images": 0,
            "skipped_barcodes": 0,
            "errors": [],
        }

        with transaction.atomic():
            for record in payload:
                try:
                    self._import_record(
                        record,
                        images_root,
                        entry_type,
                        exit_type,
                        summary,
                        movement_site,
                    )
                except Exception as exc:
                    summary["errors"].append(
                        f"{record.get('default_code') or record.get('name') or record.get('id')}: {exc}"
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "Import terminé — produits créés: %(created)d, mis à jour: %(updated)d, "
                "mouvements: %(stock_movements)d, images copiées: %(images_saved)d, "
                "images manquantes: %(missing_images)d, codes-barres ignorés: %(skipped_barcodes)d."
                % summary
            )
        )
        if summary["errors"]:
            self.stdout.write(self.style.ERROR("Erreurs rencontrées:"))
            for error in summary["errors"]:
                self.stdout.write(f"- {error}")

    def _import_record(
        self,
        record: dict,
        images_root: Path,
        entry_type: MovementType,
        exit_type: MovementType,
        summary: dict,
        site,
    ):
        sku = _build_sku(record)
        brand = _ensure_brand(record.get("brand"))
        category = _ensure_category(_category_main_name(record))
        subcategory = _ensure_subcategory(category, _category_sub_name(record))
        name = _clean_text(record.get("name"))
        if not name:
            identifier = record.get("odoo_id") or record.get("id")
            name = f"Produit {identifier or 'sans nom'}"
        manufacturer_reference = (
            _clean_text(record.get("default_code"))
            or _clean_text(record.get("slug"))
            or str(record.get("odoo_id") or record.get("id") or "")
        ).upper()
        description = _clean_description(record.get("description")) or _clean_description(
            record.get("short_description")
        )
        raw_barcode = _compute_barcode(record)
        barcode = _ensure_unique_barcode(raw_barcode, sku)
        if raw_barcode and not barcode:
            summary["skipped_barcodes"] += 1
        sale_price = _as_decimal(record.get("list_price"))
        defaults = {
            "name": name,
            "manufacturer_reference": manufacturer_reference,
            "brand": brand,
            "category": category,
            "subcategory": subcategory,
            "description": description,
            "barcode": barcode,
            "sale_price": sale_price,
        }
        product, created = Product.objects.get_or_create(sku=sku, defaults=defaults)
        updated = False

        if not created:
            if product.name != name:
                product.name = name
                updated = True
            if product.manufacturer_reference != manufacturer_reference:
                product.manufacturer_reference = manufacturer_reference
                updated = True
            if description and product.description != description:
                product.description = description
                updated = True
            if barcode and product.barcode != barcode:
                product.barcode = barcode
                updated = True
            if sale_price is not None and product.sale_price != sale_price:
                product.sale_price = sale_price
                updated = True
            if product.brand != brand:
                product.brand = brand
                updated = True
            if product.category != category:
                product.category = category
                updated = True
            if product.subcategory != subcategory:
                product.subcategory = subcategory
                updated = True
        if created:
            summary["created"] += 1
        elif updated:
            summary["updated"] += 1

        image_relative = record.get("local_image")
        image_path = _resolve_image_path(images_root, image_relative)
        image_saved = False
        if image_path:
            filename = _build_image_filename(sku, image_path.name)
            storage_path = Path("products/images") / filename
            if str(product.image) != str(storage_path):
                with image_path.open("rb") as handle:
                    product.image.save(filename, File(handle), save=False)
                image_saved = True
                summary["images_saved"] += 1
        elif image_relative:
            summary["missing_images"] += 1

        if created or updated or image_saved:
            product.save()

        desired_stock = 0
        stock_raw = record.get("stock_quantity")
        try:
            desired_stock = max(int(float(stock_raw or 0)), 0)
        except (ValueError, TypeError):
            desired_stock = 0
        current_stock = product.stock_quantity
        delta = desired_stock - current_stock
        if delta == 0:
            return
        movement_type = entry_type if delta > 0 else exit_type
        StockMovement.objects.create(
            product=product,
            movement_type=movement_type,
            quantity=abs(delta),
            movement_date=timezone.now(),
            comment="Import Render",
            document_number=f"RENDER-{record.get('odoo_id') or record.get('id')}",
            site=site,
        )
        summary["stock_movements"] += 1
