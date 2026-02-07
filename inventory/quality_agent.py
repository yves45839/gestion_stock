from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageStat

from .bot import ProductAssetBot
from .models import Product


@dataclass
class ProductQualityReport:
    score: int
    max_score: int
    details: dict[str, int]
    issues: list[str]


class ProductQualityAgent:
    """Audit product content quality and improve low-scoring products."""

    def __init__(self, *, threshold: int = 70, bot: ProductAssetBot | None = None):
        self.threshold = threshold
        self.bot = bot or ProductAssetBot()

    def evaluate(self, product: Product) -> ProductQualityReport:
        details: dict[str, int] = {}
        issues: list[str] = []

        details["name"] = 15 if (product.name or "").strip() else 0
        if not details["name"]:
            issues.append("Nom produit manquant.")

        description_length = len((product.description or "").strip())
        if description_length >= 220:
            details["description"] = 20
        elif description_length >= 80:
            details["description"] = 12
            issues.append("Description principale trop courte.")
        elif description_length > 0:
            details["description"] = 6
            issues.append("Description principale insuffisante.")
        else:
            details["description"] = 0
            issues.append("Description principale absente.")

        short_length = len((product.short_description or "").strip())
        if short_length >= 60:
            details["short_description"] = 10
        elif short_length > 0:
            details["short_description"] = 5
            issues.append("Description courte à enrichir.")
        else:
            details["short_description"] = 0
            issues.append("Description courte absente.")

        long_length = len((product.long_description or "").strip())
        if long_length >= 450:
            details["long_description"] = 20
        elif long_length >= 200:
            details["long_description"] = 12
            issues.append("Description longue à approfondir.")
        elif long_length > 0:
            details["long_description"] = 6
            issues.append("Description longue trop légère.")
        else:
            details["long_description"] = 0
            issues.append("Description longue absente.")

        specs_count = self._spec_count(product.tech_specs_json)
        if specs_count >= 8:
            details["tech_specs"] = 15
        elif specs_count >= 4:
            details["tech_specs"] = 9
            issues.append("Fiche technique partielle.")
        elif specs_count > 0:
            details["tech_specs"] = 5
            issues.append("Fiche technique incomplète.")
        else:
            details["tech_specs"] = 0
            issues.append("Fiche technique absente.")

        image_analysis = self._analyze_product_image(product)
        details["image"] = image_analysis["score"]
        if image_analysis["status"] == "missing":
            issues.append("Image produit absente.")
        elif image_analysis["status"] == "fake":
            issues.append(
                "Image non exploitable détectée (placeholder/icône). "
                "Ajoutez une vraie photo produit."
            )
        elif image_analysis["status"] == "suspect":
            issues.append(
                "Image potentiellement peu exploitable (qualité faible ou visuel trop générique)."
            )

        content_bonus = 0
        if product.datasheet_url or product.datasheet_pdf:
            content_bonus += 4
        if product.video_links:
            content_bonus += 3
        if product.brochures.exists():
            content_bonus += 3
        details["content_assets"] = content_bonus
        if content_bonus < 5:
            issues.append("Peu de contenus annexes (PDF, vidéos, brochures).")

        score = sum(details.values())
        return ProductQualityReport(score=score, max_score=100, details=details, issues=issues)

    def improve_if_needed(self, product: Product) -> dict[str, Any]:
        report = self.evaluate(product)
        result: dict[str, Any] = {
            "product_id": product.id,
            "sku": product.sku,
            "score": report.score,
            "threshold": self.threshold,
            "issues": report.issues,
            "changed": False,
            "changes": {},
        }
        if report.score >= self.threshold:
            result["status"] = "ok"
            return result

        assets = ["description", "techsheet", "videos", "pdf"]
        changes = self.bot.ensure_assets(
            product,
            assets=assets,
            force_description=True,
            force_techsheet=True,
            force_pdf=False,
            force_videos=True,
            force_blog=False,
        )

        update_fields: list[str] = []
        if changes.get("short_description_changed"):
            update_fields.append("short_description")
        if changes.get("long_description_changed"):
            update_fields.append("long_description")
        if changes.get("description_changed"):
            update_fields.append("description")
        if changes.get("tech_specs_changed"):
            update_fields.append("tech_specs_json")
        if changes.get("videos_changed"):
            update_fields.append("video_links")

        if update_fields:
            product.save(update_fields=sorted(set(update_fields)))
            result["changed"] = True

        new_report = self.evaluate(product)
        result.update(
            {
                "status": "improved" if result["changed"] else "low_score_no_change",
                "changes": changes,
                "score_after": new_report.score,
                "issues_after": new_report.issues,
            }
        )
        return result

    @staticmethod
    def _spec_count(specs: Any) -> int:
        if isinstance(specs, dict):
            return len([key for key, value in specs.items() if str(key).strip() and value not in (None, "")])
        if isinstance(specs, list):
            return len([item for item in specs if item])
        return 0

    @staticmethod
    def _analyze_product_image(product: Product) -> dict[str, Any]:
        if not product.image:
            return {"status": "missing", "score": 0, "confidence": 0.0}
        if product.image_is_placeholder:
            return {"status": "fake", "score": 1, "confidence": 1.0}

        image_name = str(product.image).lower()
        placeholder_markers = (
            "placeholder",
            "no-image",
            "no_image",
            "dummy",
            "default",
            "fallback",
            "blank",
        )
        if any(marker in image_name for marker in placeholder_markers):
            return {"status": "fake", "score": 1, "confidence": 0.95}

        try:
            with product.image.open("rb") as handle:
                image = Image.open(handle)
                image.load()
        except Exception:
            return {"status": "fake", "score": 0, "confidence": 1.0}

        image = image.convert("RGB")

        width, height = image.size
        smallest_side = min(width, height)
        ratio = (max(width, height) / smallest_side) if smallest_side else 999
        if smallest_side < 120:
            return {"status": "fake", "score": 1, "confidence": 0.9}
        if ratio > 4.0:
            return {"status": "fake", "score": 1, "confidence": 0.9}

        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        variance = float(stat.var[0])
        dynamic_range = float(stat.extrema[0][1] - stat.extrema[0][0])

        # Détection des visuels trop plats (fonds unis / placeholders simples)
        if variance < 40 or dynamic_range < 55:
            return {"status": "fake", "score": 2, "confidence": 0.85}

        # Palette trop pauvre = souvent logo/icône plutôt qu'une photo réelle produit.
        unique_colors = image.getcolors(maxcolors=256)
        unique_count = len(unique_colors) if unique_colors else 256
        if unique_count < 12:
            return {"status": "fake", "score": 2, "confidence": 0.8}

        # Images jugées exploitables mais avec une qualité visuelle moyenne.
        if variance < 95 or dynamic_range < 85 or unique_count < 32:
            return {"status": "suspect", "score": 6, "confidence": 0.6}

        return {"status": "real", "score": 10, "confidence": 0.8}
