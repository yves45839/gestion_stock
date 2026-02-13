from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import Product

logger = logging.getLogger(__name__)

_HYPHENS = {
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2212": "-",
}

_DEFAULT_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
_DEFAULT_SERPER_ENDPOINT = "https://google.serper.dev/search"


def _normalize_model(value: str) -> str:
    if not value:
        return ""
    for key, replacement in _HYPHENS.items():
        value = value.replace(key, replacement)
    value = value.upper().strip()
    return re.sub(r"[^A-Z0-9]+", "", value)


def _strip_unicode_hyphens(value: str) -> str:
    if not value:
        return ""
    for key, replacement in _HYPHENS.items():
        value = value.replace(key, replacement)
    return value


def extract_model(product: Product) -> str:
    candidates = [
        product.manufacturer_reference or "",
        product.sku or "",
        product.name or "",
    ]
    for raw in candidates:
        cleaned = _strip_unicode_hyphens(raw)
        match = re.search(r"(DS-[A-Z0-9-]+)", cleaned, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    base = next((value for value in candidates if value), "")
    base = _strip_unicode_hyphens(base).strip()
    base = re.sub(r"-\d+$", "", base, flags=re.IGNORECASE)
    return base


def build_query(model: str, prefer_lang: str = "fr", domain: str = "hikvision.com") -> str:
    if prefer_lang == "en":
        keywords = '(datasheet OR "data sheet")'
    elif prefer_lang == "any":
        keywords = '(datasheet OR "data sheet" OR "fiche technique" OR "fiche-technique")'
    else:
        keywords = '("fiche technique" OR "fiche-technique" OR datasheet OR "data sheet")'
    query = f'site:{domain} "{model}" {keywords} filetype:pdf'
    return query


def resolve_brand_datasheet_domain(brand_name: str) -> str:
    normalized = (brand_name or "").strip().lower()
    if normalized == "dahua":
        return "dahuasecurity.com"
    return "hikvision.com"


def _get_cse_credentials() -> tuple[str, str, str]:
    api_key = getattr(settings, "GOOGLE_CSE_API_KEY", None) or getattr(
        settings, "GOOGLE_CUSTOM_SEARCH_API_KEY", None
    )
    engine_id = getattr(settings, "GOOGLE_CSE_CX", None) or getattr(
        settings, "GOOGLE_CUSTOM_SEARCH_ENGINE_ID", None
    )
    endpoint = getattr(settings, "GOOGLE_CSE_ENDPOINT", None) or _DEFAULT_CSE_ENDPOINT
    return api_key or "", engine_id or "", endpoint


def _get_serper_credentials() -> tuple[str, str]:
    api_key = getattr(settings, "SERPER_API_KEY", None)
    endpoint = getattr(settings, "SERPER_SEARCH_ENDPOINT", None) or _DEFAULT_SERPER_ENDPOINT
    return api_key or "", endpoint


def google_cse_search(
    session: requests.Session,
    query: str,
    *,
    num: int = 10,
) -> dict:
    api_key, engine_id, endpoint = _get_cse_credentials()
    if not api_key or not engine_id:
        raise RuntimeError("Google CSE config missing (GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX).")
    params = {
        "key": api_key,
        "cx": engine_id,
        "q": query,
        "num": num,
        "fileType": "pdf",
        "fields": "items(link,title,snippet,mime)",
    }
    response = session.get(endpoint, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def serper_search(
    session: requests.Session,
    query: str,
    *,
    num: int = 10,
) -> dict:
    api_key, endpoint = _get_serper_credentials()
    if not api_key:
        raise RuntimeError("Serper config missing (SERPER_API_KEY).")
    payload = {
        "q": query,
        "num": max(1, min(int(num or 10), 10)),
    }
    response = session.post(
        endpoint,
        json=payload,
        timeout=30,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
    )
    response.raise_for_status()
    return response.json()


def _serper_to_cse_items(data: dict) -> list[dict]:
    items: list[dict] = []
    for candidate in data.get("organic") or []:
        link = candidate.get("link") or ""
        title = candidate.get("title") or ""
        snippet = candidate.get("snippet") or ""
        if not link:
            continue
        mime = "application/pdf" if ".pdf" in link.lower() else ""
        items.append({"link": link, "title": title, "snippet": snippet, "mime": mime})
    return items


def search_datasheet_pdf(
    session: requests.Session,
    query: str,
    model: str,
    *,
    prefer_lang: str = "fr",
    num: int = 10,
) -> tuple[Optional[str], str]:
    search_errors: list[str] = []
    try:
        serper_data = serper_search(session, query, num=num)
        serper_items = _serper_to_cse_items(serper_data)
        best = pick_best_pdf(serper_items, model, prefer_lang=prefer_lang)
        if best:
            return best, "serper"
        search_errors.append("Serper: no PDF result found")
    except Exception as exc:  # noqa: BLE001
        search_errors.append(f"Serper: {exc}")

    try:
        cse_data = google_cse_search(session, query, num=num)
        cse_items = cse_data.get("items") or []
        best = pick_best_pdf(cse_items, model, prefer_lang=prefer_lang)
        if best:
            return best, "google_cse"
        search_errors.append("Google CSE: no PDF result found")
    except Exception as exc:  # noqa: BLE001
        search_errors.append(f"Google CSE: {exc}")

    raise RuntimeError("; ".join(search_errors) or "No PDF result found.")


def score_result(item: dict, model: str, prefer_lang: str = "fr") -> int:
    url = (item.get("link") or "").lower()
    title = (item.get("title") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    blob = f"{url} {title} {snippet}"

    score = 0
    if url.endswith(".pdf") or ".pdf?" in url:
        score += 50
    if (item.get("mime") or "").lower() == "application/pdf":
        score += 15
    if "hikvision.com" in url:
        score += 30
    if "datasheet" in blob or "data sheet" in blob or "fiche" in blob:
        score += 20

    normalized_model = _normalize_model(model)
    if normalized_model and normalized_model in _normalize_model(url + title):
        score += 50

    if prefer_lang == "fr":
        if "/fr/" in url or "fiche" in blob:
            score += 10
    elif prefer_lang == "en":
        if "/en/" in url or "datasheet" in blob:
            score += 10

    if "firmware" in blob:
        score -= 40
    if "manual" in blob or "user manual" in blob:
        score -= 20

    return score


def pick_best_pdf(items: Iterable[dict], model: str, prefer_lang: str = "fr") -> Optional[str]:
    best_url = None
    best_score = -10**9
    for item in items:
        score = score_result(item, model, prefer_lang=prefer_lang)
        if score > best_score:
            best_score = score
            best_url = item.get("link")
    return best_url


def _extract_pdf_link_from_html(html: str, base_url: str) -> Optional[str]:
    if not html:
        return None
    candidates = re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, re.IGNORECASE)
    if not candidates:
        return None
    prioritized = []
    for candidate in candidates:
        score = 0
        lower = candidate.lower()
        if "datasheet" in lower or "data_sheet" in lower or "fiche" in lower:
            score += 2
        if "manual" in lower or "firmware" in lower:
            score -= 1
        prioritized.append((score, candidate))
    prioritized.sort(key=lambda item: (-item[0], item[1]))
    return urljoin(base_url, prioritized[0][1])


def download_pdf_streaming(
    session: requests.Session,
    url: str,
    *,
    max_mb: int = 20,
    html_limit_kb: int = 512,
    allow_html_fallback: bool = True,
) -> tuple[str, bytes, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DatasheetBot/1.0)",
    }
    response = session.get(url, stream=True, timeout=60, allow_redirects=True, headers=headers)
    response.raise_for_status()

    content_type = (response.headers.get("Content-Type") or "").lower()
    is_html = "text/html" in content_type
    byte_limit = (html_limit_kb * 1024) if is_html else (max_mb * 1024 * 1024)

    chunks = []
    hasher = hashlib.sha256()
    total = 0
    for chunk in response.iter_content(chunk_size=256 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > byte_limit:
            if is_html:
                break
            raise ValueError(f"PDF too large (> {max_mb} MB): {response.url}")
        hasher.update(chunk)
        chunks.append(chunk)

    content = b"".join(chunks)
    if content.startswith(b"%PDF"):
        return response.url, content, hasher.hexdigest()

    if is_html and allow_html_fallback:
        html = content.decode(errors="ignore")
        fallback_url = _extract_pdf_link_from_html(html, response.url)
        if fallback_url and fallback_url != url:
            return download_pdf_streaming(
                session,
                fallback_url,
                max_mb=max_mb,
                html_limit_kb=html_limit_kb,
                allow_html_fallback=False,
            )

    raise ValueError(f"Downloaded file is not a PDF (content-type={content_type}) url={response.url}")


def _safe_filename(model: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", model or "").strip("_")
    if not cleaned:
        cleaned = "datasheet"
    return f"{cleaned}.pdf"


@dataclass
class DatasheetSummary:
    products: int
    models: int
    updated: int
    skipped: int
    failed: int
    errors: list[dict]


def fetch_hikvision_datasheets(
    *,
    queryset: Optional[Iterable[Product]] = None,
    brand_name: str = "HIKVISION",
    limit: Optional[int] = None,
    prefer_lang: str = "fr",
    force: bool = False,
    dry_run: bool = False,
    domain: Optional[str] = None,
) -> DatasheetSummary:
    if queryset is None:
        queryset = Product.objects.select_related("brand").filter(
            brand__name__iexact=brand_name
        )
    if limit:
        try:
            queryset = list(queryset[:limit])
        except TypeError:
            queryset = list(queryset)
            queryset = queryset[:limit]
    else:
        queryset = list(queryset)

    buckets: dict[str, list[Product]] = {}
    errors: list[dict] = []
    for product in queryset:
        model = extract_model(product)
        if not model:
            errors.append(
                {"model": "", "product_id": product.id, "error": "Missing model reference."}
            )
            continue
        buckets.setdefault(model, []).append(product)

    updated = 0
    skipped = 0
    failed = 0
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        }
    )
    max_mb = int(getattr(settings, "HIKVISION_DATASHEET_MAX_MB", 20))
    html_limit_kb = int(getattr(settings, "HIKVISION_DATASHEET_HTML_LIMIT_KB", 512))
    sleep_s = float(getattr(settings, "HIKVISION_DATASHEET_SLEEP", 1.0))

    search_domain = domain or resolve_brand_datasheet_domain(brand_name)

    for model, products in buckets.items():
        existing_pdf = next((p.datasheet_pdf for p in products if p.datasheet_pdf), None)
        existing_url = next((p.datasheet_url for p in products if p.datasheet_url), None)
        if existing_pdf and not force:
            for product in products:
                if product.datasheet_pdf:
                    skipped += 1
                    continue
                if not dry_run:
                    product.datasheet_pdf.name = existing_pdf.name
                    if existing_url and not product.datasheet_url:
                        product.datasheet_url = existing_url
                    product.datasheet_fetched_at = timezone.now()
                    product.save(update_fields=["datasheet_pdf", "datasheet_url", "datasheet_fetched_at"])
                updated += 1
            continue
        if not force and all(product.datasheet_pdf for product in products):
            skipped += len(products)
            continue

        query = build_query(model, prefer_lang=prefer_lang, domain=search_domain)
        try:
            best, source = search_datasheet_pdf(
                session,
                query,
                model,
                prefer_lang=prefer_lang,
                num=10,
            )

            if dry_run:
                updated += len(products)
                continue

            final_url, content, sha256 = download_pdf_streaming(
                session,
                best,
                max_mb=max_mb,
                html_limit_kb=html_limit_kb,
            )
            filename = _safe_filename(model)

            stored_name = None
            for product in products:
                if stored_name:
                    product.datasheet_pdf.name = stored_name
                else:
                    product.datasheet_pdf.save(filename, ContentFile(content), save=False)
                    stored_name = product.datasheet_pdf.name
                product.datasheet_url = final_url
                product.datasheet_fetched_at = timezone.now()
                product.save(update_fields=["datasheet_url", "datasheet_pdf", "datasheet_fetched_at"])
                updated += 1
            logger.info("Downloaded datasheet for %s via %s (sha256=%s)", model, source, sha256)
        except Exception as exc:  # noqa: BLE001
            failed += len(products)
            errors.append({"model": model, "error": str(exc)})
            logger.warning("Datasheet fetch failed for %s: %s", model, exc)
        finally:
            if sleep_s:
                time.sleep(sleep_s)

    return DatasheetSummary(
        products=len(queryset),
        models=len(buckets),
        updated=updated,
        skipped=skipped,
        failed=failed,
        errors=errors,
    )
