import json
import logging
import mimetypes
import re
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote_plus, urlparse

import requests
from mistralai import Mistral
from mistralai.models import UserMessage
from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile

try:
    from pypdf import PdfReader
except Exception:  # noqa: BLE001
    PdfReader = None

from .models import ProductAsset, ProductBrochure

logger = logging.getLogger(__name__)


class MistralTextGenerator:
    """Thin client for Mistral's SDK."""

    def __init__(self, api_key: str, model: str = "mistral-medium-latest", agent_id: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.agent_id = agent_id
        self.client = Mistral(api_key=self.api_key)

    def generate_text(self, prompt: str, temperature: float = 0.35, max_tokens: int = 400) -> Optional[str]:
        if not self.api_key:
            return None
        try:
            if self.agent_id:
                response = self.client.agents.complete(
                    agent_id=self.agent_id,
                    messages=[UserMessage(content=prompt)],
                    max_tokens=max_tokens,
                )
            else:
                response = self.client.chat.complete(
                    model=self.model,
                    messages=[UserMessage(content=prompt)],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mistral request failed (%s): %s", self.agent_id or self.model, exc)
            return None
        return self._extract_text(response)

    def _extract_text(self, payload: Dict[str, Any]) -> Optional[str]:
        candidates = getattr(payload, "choices", None)
        if not candidates and isinstance(payload, dict):
            candidates = payload.get("choices")
        if not candidates:
            candidates = getattr(payload, "outputs", None)
        if not candidates and isinstance(payload, dict):
            candidates = payload.get("outputs")
        candidates = candidates or [payload]

        for candidate in candidates:
            message = getattr(candidate, "message", None) if not isinstance(candidate, dict) else candidate.get("message")
            content = getattr(message, "content", None) if message is not None else None
            if content is None:
                content = candidate.get("content") if isinstance(candidate, dict) else candidate
            text = self._text_from_content(content)
            if text:
                return text.strip()
        return None

    def _text_from_content(self, content: Any) -> Optional[str]:
        if isinstance(content, str):
            return content
        if hasattr(content, "text"):
            return getattr(content, "text")
        if isinstance(content, dict):
            for key in ("text", "output_text", "message"):
                value = content.get(key)
                if isinstance(value, str):
                    return value
            nested = content.get("content")
            if isinstance(nested, Iterable):
                for item in nested:
                    text = self._text_from_content(item)
                    if text:
                        return text
        if isinstance(content, Iterable) and not isinstance(content, str):
            for item in content:
                text = self._text_from_content(item)
                if text:
                    return text
        return None


class _DailyQuota:
    def __init__(self, path: Path, daily_limit: int):
        self.path = path
        self.daily_limit = daily_limit
        self._lock = Lock()

    def reserve(self) -> bool:
        if self.daily_limit <= 0:
            return False
        today = date.today().isoformat()
        with self._lock:
            data = self._read()
            if data.get("date") != today:
                data = {"date": today, "count": 0}
            if data.get("count", 0) >= self.daily_limit:
                return False
            data["count"] = int(data.get("count", 0)) + 1
            self._write(data)
        return True

    def _read(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _write(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        except OSError:
            return


class GoogleImageSearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        engine_id: str,
        safe: str,
        daily_limit: int,
        session: requests.Session,
        timeout: int,
        usage_path: Path,
    ):
        self.api_key = api_key
        self.engine_id = engine_id
        self.safe = safe or "active"
        self.daily_limit = daily_limit
        self.session = session
        self.timeout = timeout
        self.quota = _DailyQuota(usage_path, daily_limit)
        self.last_status = None
        self.last_error = None
        self.last_query = None

    def search_image(self, query: str) -> Optional[str]:
        self.last_status = None
        self.last_error = None
        self.last_query = query
        if not query:
            self.last_status = "empty_query"
            return None
        if not self.api_key or not self.engine_id:
            self.last_status = "missing_config"
            return None
        if not self.quota.reserve():
            self.last_status = "quota"
            logger.info("Google image search quota reached (%s/day).", self.daily_limit)
            return None
        params = {
            "key": self.api_key,
            "cx": self.engine_id,
            "q": query,
            "searchType": "image",
            "num": 1,
            "safe": self.safe,
            "fields": "items(link,mime,image/contextLink)",
        }
        try:
            response = self.session.get(
                "https://www.googleapis.com/customsearch/v1",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self.last_status = "request_error"
            self.last_error = str(exc)
            logger.warning("Google image search failed: %s", exc)
            return None
        try:
            payload = response.json()
        except ValueError:
            self.last_status = "bad_json"
            return None
        items = payload.get("items") or []
        if not items:
            self.last_status = "no_results"
            return None
        self.last_status = "ok"
        return items[0].get("link")


class SerperImageSearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        daily_limit: int,
        session: requests.Session,
        timeout: int,
        usage_path: Path,
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.daily_limit = daily_limit
        self.session = session
        self.timeout = timeout
        self.quota = _DailyQuota(usage_path, daily_limit)
        self.last_status = None
        self.last_error = None
        self.last_query = None

    def search_image(self, query: str) -> Optional[str]:
        self.last_status = None
        self.last_error = None
        self.last_query = query
        if not query:
            self.last_status = "empty_query"
            return None
        if not self.api_key:
            self.last_status = "missing_config"
            return None
        if not self.quota.reserve():
            self.last_status = "quota"
            logger.info("Serper image search quota reached (%s/day).", self.daily_limit)
            return None
        payload = {"q": query, "num": 1}
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self.last_status = "request_error"
            self.last_error = str(exc)
            logger.warning("Serper image search failed: %s", exc)
            return None
        try:
            data = response.json()
        except ValueError:
            self.last_status = "bad_json"
            return None
        items = data.get("images") or data.get("image_results") or []
        if not items:
            self.last_status = "no_results"
            return None
        image = items[0] or {}
        for key in ("imageUrl", "link", "thumbnailUrl", "sourceUrl", "url"):
            url = image.get(key)
            if url:
                self.last_status = "ok"
                return url
        self.last_status = "no_results"
        return None


class ProductAssetBot:
    def __init__(
        self,
        text_generator: Optional[MistralTextGenerator] = None,
        image_url_template: Optional[str] = None,
        image_timeout: Optional[int] = None,
    ):
        self.text_generator = (
            text_generator
            if text_generator
            else self._build_text_generator(settings.MISTRAL_API_KEY)
        )
        self.image_url_template = image_url_template or settings.PRODUCT_BOT_IMAGE_URL_TEMPLATE
        self.image_timeout = image_timeout or settings.PRODUCT_BOT_IMAGE_TIMEOUT
        self.image_session = requests.Session()
        self.image_session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            }
        )
        self.allow_placeholders = getattr(settings, "PRODUCT_BOT_ALLOW_PLACEHOLDERS", False)
        self.placeholder_domains = {
            domain.lower()
            for domain in getattr(
                settings,
                "PRODUCT_BOT_PLACEHOLDER_DOMAINS",
                ("dummyimage.com", "via.placeholder.com", "placehold.co"),
            )
        }
        self.last_image_log = None
        self.last_google_status = None
        self.last_google_query = None
        self.google_search_status = "disabled"
        self.google_search = self._build_google_search()
        self.last_serper_status = None
        self.last_serper_query = None
        self.serper_search_status = "disabled"
        self.serper_search = self._build_serper_search()

    def _build_text_generator(self, api_key: Optional[str]) -> Optional[MistralTextGenerator]:
        if not api_key:
            return None
        return MistralTextGenerator(
            api_key=api_key,
            model=settings.MISTRAL_MODEL,
            agent_id=settings.MISTRAL_AGENT_ID,
        )

    def _build_google_search(self) -> Optional[GoogleImageSearchClient]:
        enabled = getattr(settings, "PRODUCT_BOT_GOOGLE_IMAGE_SEARCH_ENABLED", False)
        if not enabled:
            self.google_search_status = "disabled"
            return None
        api_key = getattr(settings, "GOOGLE_CUSTOM_SEARCH_API_KEY", None)
        engine_id = getattr(settings, "GOOGLE_CUSTOM_SEARCH_ENGINE_ID", None)
        if not api_key or not engine_id:
            self.google_search_status = "missing_config"
            logger.warning("Google image search enabled but missing API key or engine id.")
            return None
        self.google_search_status = "enabled"
        daily_limit = getattr(settings, "PRODUCT_BOT_GOOGLE_IMAGE_DAILY_LIMIT", 0)
        safe = getattr(settings, "PRODUCT_BOT_GOOGLE_IMAGE_SAFE", "active")
        usage_path = Path(settings.BASE_DIR) / "var" / "google_cse_usage.json"
        return GoogleImageSearchClient(
            api_key=api_key,
            engine_id=engine_id,
            safe=safe,
            daily_limit=daily_limit,
            session=self.image_session,
            timeout=self.image_timeout,
            usage_path=usage_path,
        )

    def _build_serper_search(self) -> Optional[SerperImageSearchClient]:
        enabled = getattr(settings, "PRODUCT_BOT_SERPER_IMAGE_SEARCH_ENABLED", False)
        if not enabled:
            self.serper_search_status = "disabled"
            return None
        api_key = getattr(settings, "SERPER_API_KEY", None)
        if not api_key:
            self.serper_search_status = "missing_config"
            logger.warning("Serper image search enabled but missing API key.")
            return None
        self.serper_search_status = "enabled"
        daily_limit = getattr(settings, "PRODUCT_BOT_SERPER_IMAGE_DAILY_LIMIT", 0)
        endpoint = getattr(settings, "SERPER_IMAGE_ENDPOINT", "https://google.serper.dev/images")
        usage_path = Path(settings.BASE_DIR) / "var" / "serper_usage.json"
        return SerperImageSearchClient(
            api_key=api_key,
            endpoint=endpoint,
            daily_limit=daily_limit,
            session=self.image_session,
            timeout=self.image_timeout,
            usage_path=usage_path,
        )

    def ensure_assets(
        self,
        product,
        *,
        assets: Optional[Iterable[str]] = None,
        force_description: bool = False,
        force_image: bool = False,
        force_techsheet: bool = False,
        force_pdf: bool = False,
        force_videos: bool = False,
        force_blog: bool = False,
        image_field: str = "image",
    ) -> dict[str, bool]:
        asset_set = self._normalize_assets(assets)
        changes: dict[str, bool] = {}
        if "description" in asset_set:
            changes.update(self.ensure_descriptions(product, force=force_description))
        if "images" in asset_set:
            changes["image_changed"] = self.ensure_image(product, force_image, image_field=image_field)
        if "techsheet" in asset_set:
            changes["tech_specs_changed"] = self.ensure_tech_specs(product, force=force_techsheet)
        if "pdf" in asset_set:
            changes["pdf_changed"] = self.ensure_pdf_brochures(product, force=force_pdf)
        if "videos" in asset_set:
            changes["videos_changed"] = self.ensure_video_links(product, force=force_videos)
        if "blog" in asset_set:
            changes["blog_changed"] = self.ensure_blog_post(product, force=force_blog)
        return changes

    def _normalize_assets(self, assets: Optional[Iterable[str]]) -> set[str]:
        if not assets:
            return {"description", "images"}
        normalized = {item.strip().lower() for item in assets if item}
        return normalized or {"description", "images"}

    def ensure_descriptions(self, product, force: bool = False) -> dict[str, bool]:
        changes = {
            "short_description_changed": False,
            "long_description_changed": False,
            "description_changed": False,
        }
        if not self.text_generator:
            return changes
        if not product.short_description or force:
            short_prompt = self._build_short_description_prompt(product)
            short_desc = self.text_generator.generate_text(short_prompt, max_tokens=200)
            if short_desc:
                cleaned = short_desc.strip()
                if cleaned and cleaned != (product.short_description or "").strip():
                    product.short_description = cleaned
                    changes["short_description_changed"] = True
        if not product.long_description or force:
            long_prompt = self._build_long_description_prompt(product)
            long_desc = self.text_generator.generate_text(long_prompt, max_tokens=650)
            if long_desc:
                cleaned = long_desc.strip()
                if cleaned and cleaned != (product.long_description or "").strip():
                    product.long_description = cleaned
                    changes["long_description_changed"] = True
                if cleaned and cleaned != (product.description or "").strip():
                    product.description = cleaned
                    changes["description_changed"] = True
        if any(changes.values()):
            ProductAsset.objects.update_or_create(
                product=product,
                asset_type=ProductAsset.AssetType.DESCRIPTION,
                defaults={
                    "text_content": json.dumps(
                        {
                            "short_description": product.short_description,
                            "long_description": product.long_description,
                        },
                        ensure_ascii=False,
                    ),
                    "metadata": {"source": "mistral"},
                    "status": ProductAsset.Status.DRAFT,
                },
            )
        return changes

    def ensure_image(self, product, force: bool = False, *, image_field: str = "image") -> bool:
        self.last_image_log = None
        field = getattr(product, image_field)
        placeholder_field = (
            "pending_image_is_placeholder" if image_field == "pending_image" else "image_is_placeholder"
        )
        is_placeholder = bool(getattr(product, placeholder_field, False))
        if field and not force and not is_placeholder:
            self._set_image_log("skip", "already has image")
            return False
        local_path = self._find_local_image(product)
        if local_path:
            applied = self._apply_local_image(product, local_path, image_field=image_field)
            if applied:
                setattr(product, placeholder_field, False)
                self._set_image_log("ok", f"local file {local_path.name}")
                ProductAsset.objects.update_or_create(
                    product=product,
                    asset_type=ProductAsset.AssetType.IMAGE,
                    defaults={
                        "source_url": "",
                        "metadata": {"source": "local", "filename": local_path.name},
                        "status": ProductAsset.Status.DRAFT,
                    },
                )
            else:
                self._set_image_log("skip", f"local file already set ({local_path.name})")
            return applied
        image_url, image_source = self._find_search_image(product)
        if not image_url:
            image_url = self._build_image_url(product)
            image_source = "template" if image_url else None
        if not image_url:
            reason = self._format_search_status() or "no_image_source"
            self._set_image_log("skip", reason)
            return False
        is_placeholder = self._is_placeholder_url(image_url)
        if is_placeholder and not self.allow_placeholders:
            detail = "placeholder blocked"
            search_status = self._format_search_status()
            if search_status and self.last_google_status != "ok" and self.last_serper_status != "ok":
                detail = f"{detail} ({search_status})"
            self._set_image_log("skip", detail)
            logger.info("Skipping placeholder image url for %s", product)
            return False
        try:
            response = self.image_session.get(image_url, timeout=self.image_timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            source_label = image_source or "url"
            self._set_image_log("fail", f"download {source_label} error")
            logger.warning("Unable to download product image for %s: %s", product, exc)
            return False
        if not self._is_image_response(response):
            source_label = image_source or "url"
            self._set_image_log("fail", f"not image from {source_label}")
            logger.warning("Downloaded payload is not an image for %s", product)
            return False
        filename = self._build_image_filename(
            product,
            source_name=self._image_source_name(image_url),
            extension=self._image_extension(response, image_url),
        )
        field.save(filename, ContentFile(response.content), save=False)
        setattr(product, placeholder_field, is_placeholder)
        source_label = image_source or "url"
        self._set_image_log("ok", f"downloaded from {source_label}")
        ProductAsset.objects.update_or_create(
            product=product,
            asset_type=ProductAsset.AssetType.IMAGE,
            defaults={
                "source_url": image_url or "",
                "metadata": {"source": image_source or "url"},
                "status": ProductAsset.Status.DRAFT,
            },
        )
        return True

    def _build_common_details(self, product) -> list[str]:
        details = [
            f"Produit: {product.name}",
            f"SKU: {product.sku}",
        ]
        if brand := getattr(product, "brand", None):
            details.append(f"Marque: {brand}")
        if category := getattr(product, "category", None):
            details.append(f"Catégorie: {category}")
        if product.sale_price:
            details.append(f"Prix de vente: {product.sale_price} FCFA")
        if product.purchase_price:
            details.append(f"Prix d'achat: {product.purchase_price} FCFA")
        if existing := (product.description or "").strip():
            details.append(f"Description existante: {existing}")
        datasheet_excerpt = self._datasheet_excerpt(product)
        if datasheet_excerpt:
            details.append(f"Extraits fiche technique: {datasheet_excerpt}")
        elif product.datasheet_url:
            details.append(f"Fiche technique: {product.datasheet_url}")
        return details

    def _build_short_description_prompt(self, product) -> str:
        details = self._build_common_details(product)
        return (
            "Tu es un assistant marketing en francais. "
            "Redige une description courte en 2-3 bullets maximum avec les avantages clefs.\n"
            "N'invente pas de caracteristiques absentes.\n"
            "Donnees produit:\n"
            + "\n".join(details)
        )

    def _build_long_description_prompt(self, product) -> str:
        details = self._build_common_details(product)
        return (
            "Tu es un assistant marketing en francais. "
            "Redige une description longue avec 2 paragraphes puis une mini FAQ (2 questions) en fin.\n"
            "Si des extraits de fiche technique sont fournis, base-toi dessus pour les caracteristiques.\n"
            "N'invente pas de caracteristiques absentes et garde les prix en FCFA.\n"
            "Donnees produit:\n"
            + "\n".join(details)
        )

    def _datasheet_excerpt(self, product) -> str:
        if not product.datasheet_pdf:
            return ""
        if PdfReader is None:
            return ""
        max_pages = int(getattr(settings, "PRODUCT_BOT_DATASHEET_MAX_PAGES", 2))
        max_chars = int(getattr(settings, "PRODUCT_BOT_DATASHEET_MAX_CHARS", 1200))
        try:
            with product.datasheet_pdf.open("rb") as handle:
                reader = PdfReader(handle)
                chunks = []
                for page in reader.pages[: max_pages or 1]:
                    try:
                        text = page.extract_text() or ""
                    except Exception:
                        continue
                    if text:
                        chunks.append(text)
                    if sum(len(chunk) for chunk in chunks) >= max_chars:
                        break
        except Exception:
            return ""
        if not chunks:
            return ""
        cleaned = re.sub(r"\s+", " ", " ".join(chunks)).strip()
        if max_chars and len(cleaned) > max_chars:
            trimmed = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
            return trimmed or cleaned[:max_chars]
        return cleaned

    def ensure_tech_specs(self, product, force: bool = False) -> bool:
        if not self.text_generator:
            return False
        if product.tech_specs_json and not force:
            return False
        datasheet_excerpt = self._datasheet_excerpt(product)
        if not datasheet_excerpt:
            return False
        prompt = (
            "Tu es un assistant qui convertit des fiches techniques en JSON strict.\n"
            "Retourne uniquement un JSON valide sans commentaire.\n"
            "Format attendu: {\"specs\": [{\"label\": \"\", \"value\": \"\"}]}.\n"
            f"Extraits fiche technique:\n{datasheet_excerpt}"
        )
        response = self.text_generator.generate_text(prompt, max_tokens=400)
        if not response:
            return False
        parsed = self._extract_json(response)
        if not parsed:
            return False
        product.tech_specs_json = parsed
        ProductAsset.objects.update_or_create(
            product=product,
            asset_type=ProductAsset.AssetType.SPECS,
            defaults={
                "text_content": json.dumps(parsed, ensure_ascii=False),
                "metadata": {"source": "datasheet"},
                "status": ProductAsset.Status.DRAFT,
            },
        )
        return True

    def ensure_pdf_brochures(self, product, force: bool = False) -> bool:
        if not product.datasheet_pdf and not product.datasheet_url:
            return False
        existing = ProductBrochure.objects.filter(product=product)
        if existing.exists() and not force:
            return False
        brochure = ProductBrochure.objects.create(
            product=product,
            title=f"Brochure {product.name}",
            source_url=product.datasheet_url or "",
        )
        if product.datasheet_pdf:
            brochure.file.name = product.datasheet_pdf.name
            brochure.save(update_fields=["file", "updated_at"])
        summary = ""
        if self.text_generator and product.datasheet_pdf:
            excerpt = self._datasheet_excerpt(product)
            if excerpt:
                prompt = (
                    "Tu es un assistant qui resume des brochures PDF en francais.\n"
                    "Fournis un resume structure en 3-5 points cles.\n"
                    f"Texte:\n{excerpt}"
                )
                summary = self.text_generator.generate_text(prompt, max_tokens=220) or ""
        ProductAsset.objects.create(
            product=product,
            asset_type=ProductAsset.AssetType.PDF,
            source_url=product.datasheet_url or "",
            file=brochure.file if brochure.file else None,
            text_content=summary.strip(),
            metadata={"source": "datasheet_pdf"},
            status=ProductAsset.Status.DRAFT,
        )
        return True

    def ensure_video_links(self, product, force: bool = False) -> bool:
        if product.video_links and not force:
            return False
        links = self._build_video_links(product)
        if not links:
            return False
        product.video_links = links
        ProductAsset.objects.update_or_create(
            product=product,
            asset_type=ProductAsset.AssetType.VIDEO,
            defaults={
                "text_content": json.dumps(links, ensure_ascii=False),
                "metadata": {"source": "search"},
                "status": ProductAsset.Status.DRAFT,
            },
        )
        return True

    def ensure_blog_post(self, product, force: bool = False) -> bool:
        if not self.text_generator:
            return False
        existing = ProductAsset.objects.filter(
            product=product,
            asset_type=ProductAsset.AssetType.BLOG,
        ).first()
        if existing and not force:
            return False
        prompt = self._build_blog_prompt(product)
        content = self.text_generator.generate_text(prompt, max_tokens=900)
        if not content:
            return False
        ProductAsset.objects.update_or_create(
            product=product,
            asset_type=ProductAsset.AssetType.BLOG,
            defaults={
                "text_content": content.strip(),
                "metadata": {"source": "mistral"},
                "status": ProductAsset.Status.DRAFT,
            },
        )
        return True

    def _build_blog_prompt(self, product) -> str:
        details = self._build_common_details(product)
        return (
            "Tu es un redacteur SEO en francais.\n"
            "Redige un article blog structure avec:\n"
            "- un plan (titres H2/H3)\n"
            "- des paragraphes courts\n"
            "- une FAQ SEO (3 questions)\n"
            "- une meta description (160 caracteres max) en fin.\n"
            "Donnees produit:\n"
            + "\n".join(details)
        )

    def _build_video_links(self, product) -> list[dict[str, str]]:
        query = self._build_google_query(product)
        if not query:
            return []
        encoded = quote_plus(query)
        return [
            {
                "platform": "youtube",
                "type": "search",
                "url": f"https://www.youtube.com/results?search_query={encoded}",
            },
            {
                "platform": "vimeo",
                "type": "search",
                "url": f"https://vimeo.com/search?q={encoded}",
            },
        ]

    @staticmethod
    def _extract_json(payload: str) -> Optional[dict]:
        cleaned = payload.strip()
        if not cleaned:
            return None
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    def _find_search_image(self, product) -> tuple[Optional[str], Optional[str]]:
        self.last_google_status = None
        self.last_google_query = None
        self.last_serper_status = None
        self.last_serper_query = None
        if not self.google_search:
            self.last_google_status = self.google_search_status
        queries = self._build_google_queries(product)
        if not queries:
            self.last_google_status = "empty_query"
            self.last_serper_status = "empty_query"
            return None, None
        max_tries = getattr(settings, "PRODUCT_BOT_GOOGLE_IMAGE_MAX_TRIES", 1)
        tries = max(max_tries or 1, 1)
        for query in queries[:tries]:
            if self.google_search:
                url = self.google_search.search_image(query)
                self.last_google_query = query
                self.last_google_status = self.google_search.last_status or "no_results"
                if url:
                    return url, "google"
            if self.serper_search:
                url = self.serper_search.search_image(query)
                self.last_serper_query = query
                self.last_serper_status = self.serper_search.last_status or "no_results"
                if url:
                    return url, "serper"
        return None, None

    def _build_google_query(self, product) -> str:
        reference = product.manufacturer_reference or product.sku or product.barcode or ""
        parts = []
        if brand := getattr(product, "brand", None):
            parts.append(str(brand))
        if reference:
            parts.append(reference)
        if product.name:
            parts.append(product.name)
        if category := getattr(product, "category", None):
            parts.append(str(category))
        return " ".join(part.strip() for part in parts if part).strip()

    def _build_google_queries(self, product) -> list[str]:
        manufacturer_reference = (product.manufacturer_reference or "").strip()
        sku = (product.sku or "").strip()
        barcode = (product.barcode or "").strip()
        reference = manufacturer_reference or sku or barcode
        brand = str(getattr(product, "brand", "") or "").strip()
        name = (product.name or "").strip()
        category = str(getattr(product, "category", "") or "").strip()
        queries = []
        if brand and manufacturer_reference:
            queries.append(f"{brand} \"{manufacturer_reference}\"")
        if brand and sku and sku != manufacturer_reference:
            queries.append(f"{brand} \"{sku}\"")
        if brand and barcode and barcode not in (manufacturer_reference, sku):
            queries.append(f"{brand} \"{barcode}\"")
        if manufacturer_reference:
            queries.append(f"\"{manufacturer_reference}\"")
        if sku and sku != manufacturer_reference:
            queries.append(f"\"{sku}\"")
        if barcode and barcode not in (manufacturer_reference, sku):
            queries.append(f"\"{barcode}\"")
        if brand and reference and name:
            queries.append(f"{brand} \"{reference}\" {name}")
        if brand and name:
            queries.append(f"{brand} {name}")
        if name and category:
            queries.append(f"{name} {category}")
        if name:
            queries.append(name)
        seen = set()
        unique = []
        for query in queries:
            cleaned = " ".join(query.split())
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                unique.append(cleaned)
        return unique

    def _format_search_status(self) -> str:
        parts = []
        if self.last_google_status:
            if self.last_google_query:
                query = self.last_google_query
                if len(query) > 60:
                    query = f"{query[:57]}..."
                parts.append(f"google: {self.last_google_status}, q: {query}")
            else:
                parts.append(f"google: {self.last_google_status}")
        if self.last_serper_status:
            if self.last_serper_query:
                query = self.last_serper_query
                if len(query) > 60:
                    query = f"{query[:57]}..."
                parts.append(f"serper: {self.last_serper_status}, q: {query}")
            else:
                parts.append(f"serper: {self.last_serper_status}")
        return " | ".join(parts)

    def _build_image_url(self, product) -> Optional[str]:
        if not self.image_url_template:
            return None
        reference = product.manufacturer_reference or product.sku or product.barcode or ""
        brand = getattr(product, "brand", None)
        category = getattr(product, "category", None)
        safe = _FormatDict(
            name=quote_plus(product.name or "produit"),
            sku=quote_plus(product.sku or ""),
            reference=quote_plus(reference),
            manufacturer_reference=quote_plus(product.manufacturer_reference or ""),
            barcode=quote_plus(product.barcode or ""),
            brand=quote_plus(str(brand)) if brand else "",
            category=quote_plus(str(category)) if category else "",
            product_id=str(product.pk or ""),
        )
        try:
            return self.image_url_template.format_map(safe)
        except KeyError:
            return self.image_url_template

    def _build_image_filename(
        self,
        product,
        *,
        source_name: Optional[str] = None,
        extension: Optional[str] = None,
    ) -> str:
        base = product.sku or product.manufacturer_reference or product.name or str(product.pk)
        slug = quote_plus(base).replace("%", "_")
        if source_name:
            cleaned_source = re.sub(r"[^0-9A-Za-z._-]+", "_", source_name).strip("_")
            filename = f"{slug[:50]}_{cleaned_source or 'image'}"
            return filename[:200]
        ext = extension or ".jpg"
        if not ext.startswith("."):
            ext = f".{ext}"
        return f"{slug[:50]}-bot{ext}"

    def _find_local_image(self, product) -> Optional[Path]:
        images_root = Path(settings.MEDIA_ROOT) / "products" / "images"
        if not images_root.exists():
            return None
        prefixes = self._image_prefixes(product)
        if not prefixes:
            return None
        for path in images_root.iterdir():
            if not path.is_file():
                continue
            name_lower = path.name.lower()
            if name_lower.endswith(("-ai.png", "-ai.jpg", "-ai.jpeg", "-ai.webp")):
                continue
            if any(name_lower.startswith(prefix) for prefix in prefixes):
                if path.stat().st_size > 0:
                    return path
        return None

    def _image_prefixes(self, product) -> list[str]:
        identifiers = [
            product.manufacturer_reference,
            product.sku,
            product.barcode,
        ]
        prefixes = []
        for raw in identifiers:
            normalized = self._normalize_identifier(raw)
            if normalized:
                prefixes.append(normalized.lower())
        return prefixes

    @staticmethod
    def _normalize_identifier(value: Optional[str]) -> str:
        if not value:
            return ""
        cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value)).strip("-_")
        return cleaned

    def _apply_local_image(self, product, path: Path, *, image_field: str = "image") -> bool:
        field = getattr(product, image_field)
        media_root = Path(settings.MEDIA_ROOT)
        try:
            relative = path.relative_to(media_root)
        except ValueError:
            with path.open("rb") as handle:
                field.save(path.name, File(handle), save=False)
            return True
        relative_name = str(relative).replace("\\", "/")
        if str(field) == relative_name:
            return False
        field.name = relative_name
        return True

    def _set_image_log(self, status: str, detail: str) -> None:
        status_label = status.strip().lower()
        detail_text = detail.strip()
        self.last_image_log = f"image {status_label}: {detail_text}" if detail_text else f"image {status_label}"

    def _is_placeholder_url(self, image_url: str) -> bool:
        parsed = urlparse(image_url)
        domain = parsed.netloc.lower()
        return domain in self.placeholder_domains

    @staticmethod
    def _is_image_response(response: requests.Response) -> bool:
        if not response.content:
            return False
        content_type = (response.headers.get("content-type") or "").lower()
        if content_type and not content_type.startswith("image/"):
            return False
        return True

    @staticmethod
    def _image_source_name(image_url: str) -> Optional[str]:
        parsed = urlparse(image_url)
        name = Path(parsed.path).name
        return name or None

    @staticmethod
    def _image_extension(response: requests.Response, image_url: str) -> Optional[str]:
        parsed = urlparse(image_url)
        ext = Path(parsed.path).suffix
        if ext:
            return ext
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        return mimetypes.guess_extension(content_type) if content_type else None


class _FormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""
