from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Pattern

from django.conf import settings
from django.core.management.base import CommandError

from inventory.models import Category, Product
from inventory.bot import MistralTextGenerator


UNCATEGORIZED_TOKENS = {
    "non classe",
    "non classee",
    "uncategorized",
    "uncategorised",
    "sans categorie",
    "sans categoriee",
}

STOPWORDS = {
    "de",
    "du",
    "des",
    "la",
    "le",
    "les",
    "et",
    "pour",
    "avec",
    "sans",
    "sur",
    "d",
}

BRAND_ALIASES = {
    "hikvision": ("hikvision", "hik-vision"),
    "dahua": ("dahua", "dhi"),
    "ezviz": ("ezviz",),
}

BRAND_DOOR_STATION_PATTERNS = {
    "hikvision": (r"\bDS-KV", r"\bDS-KD"),
    "dahua": (r"\bVTO",),
    "ezviz": (r"\bDB\d", r"\bDP\d", r"\bHP\d", r"\bCP\d"),
}

BRAND_ACCESS_CONTROL_PATTERNS = {
    "hikvision": (r"\bDS-K1", r"\bDS-K2", r"\bDS-K26", r"\bDS-K110", r"\bDS-K120"),
    "dahua": (r"\bASC", r"\bASR", r"\bASI", r"\bDHI-ASI", r"\bDHI-ASC"),
    "ezviz": (r"\bDL", r"\bL2S", r"\bL2C", r"\bL2"),
}

BRAND_ANTI_INTRUSION_PATTERNS = {
    "hikvision": (r"\bDS-P", r"\bAX\s*PRO", r"\bAXPRO"),
    "dahua": (r"\bARC", r"\bARD", r"\bARM", r"\bARA"),
}

BRAND_VIDEO_SURVEILLANCE_PATTERNS = {
    "hikvision": (
        r"\bDS-2CD",
        r"\bDS-2CE",
        r"\bDS-2DE",
        r"\bDS-2DF",
        r"\bDS-2TD",
        r"\bDS-760",
        r"\bDS-770",
        r"\bDS-780",
        r"\bDS-790",
        r"\bDS-810",
        r"\bDS-960",
    ),
    "dahua": (
        r"\bIPC",
        r"\bHFW",
        r"\bHDBW",
        r"\bHDCVI",
        r"\bXVR",
        r"\bNVR",
        r"\bSD\d",
        r"\bDH-",
    ),
    "ezviz": (r"\bCS-", r"\bC\d", r"\bH\d", r"\bBC\d", r"\bEB\d"),
}

DOOR_STATION_KEYWORDS = (
    "platine",
    "rue",
    "portier",
    "interphone",
    "visiophonie",
    "door",
    "station",
    "sonnette",
)

ACCESS_CONTROL_KEYWORDS = (
    "controle acces",
    "controle d acces",
    "access control",
    "controle",
    "acces",
    "badgeuse",
    "badge",
    "lecteur",
    "reader",
    "terminal",
    "biometrique",
    "empreinte",
    "facial",
    "clavier",
    "porte",
    "serrure",
    "gache",
    "controleur",
)

ANTI_INTRUSION_KEYWORDS = (
    "anti intrusion",
    "intrusion",
    "alarme",
    "detecteur",
    "sirene",
    "clavier",
    "magnetique",
    "pir",
    "ax pro",
    "axpro",
    "module 3g",
    "module 4g",
    "communicator",
)

VIDEO_SURVEILLANCE_KEYWORDS = (
    "videosurveillance",
    "video surveillance",
    "cctv",
    "camera",
    "camera ip",
    "ip camera",
    "dome",
    "bullet",
    "ptz",
    "enregistreur",
    "recorder",
    "nvr",
    "dvr",
    "xvr",
)

ACCESSORY_KEYWORDS = (
    "accessoire",
    "accessoires",
    "support",
    "boitier",
    "adaptateur",
    "cable",
    "connecteur",
    "alimentation",
    "alim",
    "bracket",
    "fixation",
)


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^0-9A-Za-z]+", " ", text).lower()
    return " ".join(text.split())


def _is_uncategorized(name: str) -> bool:
    normalized = _normalize(name)
    return normalized in UNCATEGORIZED_TOKENS


@dataclass
class Rule:
    category: Category
    keywords: list[str] = field(default_factory=list)
    regexes: list[Pattern[str]] = field(default_factory=list)

    def score(self, raw_text: str, normalized_text: str, tokens: set[str]) -> tuple[int, int, int]:
        score = 0
        matched_terms = 0
        best_term_length = 0
        for pattern in self.regexes:
            if pattern.search(raw_text):
                term_length = len(pattern.pattern)
                score += term_length * 3
                matched_terms += 1
                best_term_length = max(best_term_length, term_length)
        for keyword in self.keywords:
            normalized = _normalize(keyword)
            if not normalized:
                continue
            matched = False
            if " " in normalized:
                if normalized in normalized_text:
                    matched = True
            elif normalized in tokens:
                matched = True
            if not matched:
                continue
            term_length = len(normalized)
            score += term_length
            matched_terms += 1
            best_term_length = max(best_term_length, term_length)
            if " " in normalized:
                score += 2
        return score, matched_terms, best_term_length


def _build_default_rules() -> list[Rule]:
    rules: list[Rule] = []
    for category in Category.objects.order_by("name"):
        if _is_uncategorized(category.name):
            continue
        normalized = _normalize(category.name)
        if not normalized:
            continue
        tokens = [token for token in normalized.split() if token and token not in STOPWORDS]
        keywords = []
        if normalized:
            keywords.append(normalized)
        keywords.extend(tokens)
        rules.append(Rule(category=category, keywords=keywords))
    return rules


def _load_rules(path: Path) -> tuple[list[Rule], str | None]:
    if not path.exists():
        return _build_default_rules(), None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CommandError(f"Invalid JSON rules file: {exc}") from exc
    rules = []
    default_category = payload.get("default_category")
    for item in payload.get("rules", []):
        category_name = (item.get("category") or "").strip()
        if not category_name:
            continue
        category, _ = Category.objects.get_or_create(name=category_name)
        keywords = [kw for kw in (item.get("keywords") or []) if isinstance(kw, str)]
        regexes = []
        for regex in item.get("regex", []) or []:
            if not isinstance(regex, str):
                continue
            regexes.append(re.compile(regex, re.IGNORECASE))
        rules.append(Rule(category=category, keywords=keywords, regexes=regexes))
    if not rules:
        rules = _build_default_rules()
    return rules, default_category


def _pick_best_rule(rules: Iterable[Rule], raw_text: str) -> Rule | None:
    normalized_text = _normalize(raw_text)
    tokens = set(normalized_text.split())
    best_rule = None
    best_signature = (0, 0, 0, "")
    for rule in rules:
        score, matched_terms, best_term_length = rule.score(raw_text, normalized_text, tokens)
        signature = (
            score,
            matched_terms,
            best_term_length,
            _normalize(rule.category.name),
        )
        if signature > best_signature:
            best_signature = signature
            best_rule = rule
    return best_rule


def run_auto_assign_categories(
    *,
    rules_path: Path,
    apply_all: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    max_details: int | None = None,
    use_ai: bool = False,
    product_ids: Iterable[int] | None = None,
) -> dict:
    rules, default_category = _load_rules(rules_path)
    if not rules:
        raise CommandError("No category rules available.")

    ai_generator = None
    if use_ai and getattr(settings, "MISTRAL_API_KEY", None):
        ai_generator = MistralTextGenerator(
            api_key=settings.MISTRAL_API_KEY,
            model=getattr(settings, "MISTRAL_MODEL", "mistral-medium-latest"),
            agent_id=getattr(settings, "MISTRAL_AGENT_ID", None),
        )

    uncategorized_ids = []
    default_normalized = _normalize(default_category or "")
    for category in Category.objects.all():
        if _is_uncategorized(category.name) or (
            default_normalized and _normalize(category.name) == default_normalized
        ):
            uncategorized_ids.append(category.id)

    queryset = Product.objects.select_related("brand", "category").order_by("name")
    if product_ids is not None:
        normalized_ids = [int(pk) for pk in product_ids if pk]
        if not normalized_ids:
            return {
                "evaluated": 0,
                "updated": 0,
                "skipped": 0,
                "unmatched": 0,
                "changes": [],
                "change_lines": [],
                "evaluations": [],
                "evaluations_truncated": False,
                "ai_used": 0,
                "ai_attempted": 0,
                "ai_available": bool(ai_generator),
                "data_used": 0,
                "empty": True,
            }
        queryset = queryset.filter(id__in=normalized_ids)
    elif not apply_all:
        if uncategorized_ids:
            queryset = queryset.filter(category_id__in=uncategorized_ids)
        else:
            return {
                "evaluated": 0,
                "updated": 0,
                "skipped": 0,
                "unmatched": 0,
                "changes": [],
                "change_lines": [],
                "evaluations": [],
                "evaluations_truncated": False,
                "ai_used": 0,
                "ai_attempted": 0,
                "ai_available": bool(ai_generator),
                "data_used": 0,
                "empty": True,
            }

    if limit:
        queryset = queryset[:limit]

    products = list(queryset)
    updated = 0
    skipped = 0
    unmatched = 0
    ai_used = 0
    ai_attempted = 0
    data_used = 0
    changes: list[dict] = []
    change_lines: list[str] = []
    evaluations: list[dict] = []

    candidate_categories = _candidate_categories(default_category)
    categories_by_name = {category.name: category for category in candidate_categories}
    category_hints, hint_min_score = _build_category_hints(candidate_categories)

    def _append_evaluation(entry: dict) -> None:
        if max_details is None or len(evaluations) < max_details:
            evaluations.append(entry)

    for product in products:
        raw_text = _build_match_text(product)
        rule = _pick_best_rule(rules, raw_text)
        current_category = (
            product.category.name if getattr(product, "category", None) else ""
        )
        if not rule:
            suggested_category = None
            target_category = None
            source = "rules"
            brand_category = _brand_override_category(product, candidate_categories)
            if brand_category:
                suggested_category = brand_category.name
                target_category = brand_category
                source = "brand"
            else:
                data_category = _data_driven_category(
                    product,
                    category_hints,
                    hint_min_score,
                )
                if data_category:
                    suggested_category = data_category.name
                    target_category = data_category
                    source = "data"
                elif use_ai and ai_generator and candidate_categories:
                    ai_attempted += 1
                    suggested_category = _ai_pick_category(
                        ai_generator,
                        product,
                        candidate_categories,
                    )
                    source = "mistral"
                    if suggested_category:
                        target_category = categories_by_name.get(suggested_category)
            if suggested_category:
                if target_category is None:
                    target_category = categories_by_name.get(suggested_category)
                if target_category:
                    if product.category_id == target_category.id:
                        skipped += 1
                        _append_evaluation(
                            {
                                "product_id": product.id,
                                "sku": product.sku,
                                "name": product.name,
                                "current_category": current_category,
                                "suggested_category": suggested_category,
                                "status": "skipped",
                                "source": source,
                            }
                        )
                        continue
                    if dry_run:
                        changes.append(
                            {
                                "product_id": product.id,
                                "sku": product.sku,
                                "name": product.name,
                                "category": suggested_category,
                            }
                        )
                        change_lines.append(f"{product.sku} -> {suggested_category}")
                    else:
                        product.category = target_category
                        product.subcategory = None
                        product.save(update_fields=["category", "subcategory"])
                    updated += 1
                    if source == "mistral":
                        ai_used += 1
                    if source == "data":
                        data_used += 1
                    _append_evaluation(
                        {
                            "product_id": product.id,
                            "sku": product.sku,
                            "name": product.name,
                            "current_category": current_category,
                            "suggested_category": suggested_category,
                            "status": "updated",
                            "source": source,
                        }
                    )
                    continue
            unmatched += 1
            _append_evaluation(
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "current_category": current_category,
                    "suggested_category": "",
                    "status": "unmatched",
                    "source": source,
                }
            )
            continue
        suggested_category = rule.category.name
        if product.category_id == rule.category.id:
            skipped += 1
            _append_evaluation(
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "current_category": current_category,
                    "suggested_category": suggested_category,
                    "status": "skipped",
                    "source": "rules",
                }
            )
            continue
        if dry_run:
            changes.append(
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "name": product.name,
                    "category": suggested_category,
                }
            )
            change_lines.append(f"{product.sku} -> {suggested_category}")
        _append_evaluation(
            {
                "product_id": product.id,
                "sku": product.sku,
                "name": product.name,
                "current_category": current_category,
                "suggested_category": suggested_category,
                "status": "updated",
                "source": "rules",
            }
        )
        if not dry_run:
            product.category = rule.category
            product.subcategory = None
            product.save(update_fields=["category", "subcategory"])
        updated += 1

    evaluations_truncated = (
        max_details is not None and len(products) > len(evaluations)
    )
    return {
        "evaluated": len(products),
        "updated": updated,
        "skipped": skipped,
        "unmatched": unmatched,
        "changes": changes,
        "change_lines": change_lines,
        "evaluations": evaluations,
        "evaluations_truncated": evaluations_truncated,
        "ai_used": ai_used,
        "ai_attempted": ai_attempted,
        "ai_available": bool(ai_generator),
        "data_used": data_used,
        "empty": False,
    }


def _candidate_categories(default_category: str | None) -> list[Category]:
    default_normalized = _normalize(default_category or "")
    categories = []
    for category in Category.objects.order_by("name"):
        if _is_uncategorized(category.name):
            continue
        if default_normalized and _normalize(category.name) == default_normalized:
            continue
        categories.append(category)
    return categories


def _build_match_text(product: Product) -> str:
    description = (product.description or "").strip()
    if len(description) > 400:
        description = description[:400]
    brand = str(getattr(product, "brand", "") or "")
    parts = [
        product.sku,
        product.name,
        product.manufacturer_reference or "",
        product.barcode or "",
        brand,
        description,
    ]
    return " ".join(part for part in parts if part)


def _build_hint_text(row: dict) -> str:
    description = (row.get("description") or "").strip()
    if len(description) > 400:
        description = description[:400]
    brand = row.get("brand__name") or ""
    parts = [
        row.get("sku") or "",
        row.get("name") or "",
        row.get("manufacturer_reference") or "",
        row.get("barcode") or "",
        brand,
        description,
    ]
    return " ".join(part for part in parts if part)


def _tokenize_text(text: str) -> list[str]:
    normalized = _normalize(text)
    tokens = []
    for token in normalized.split():
        if not token or token in STOPWORDS:
            continue
        if len(token) <= 2:
            continue
        tokens.append(token)
    return tokens


def _build_category_hints(
    categories: list[Category],
) -> tuple[dict[int, dict], int]:
    max_products = int(getattr(settings, "CATEGORY_HINT_MAX_PRODUCTS", 2000))
    max_per_category = int(getattr(settings, "CATEGORY_HINT_MAX_PER_CATEGORY", 80))
    max_tokens = int(getattr(settings, "CATEGORY_HINT_MAX_TOKENS", 60))
    min_score = int(getattr(settings, "CATEGORY_HINT_MIN_SCORE", 4))
    name_weight = int(getattr(settings, "CATEGORY_HINT_NAME_WEIGHT", 6))
    if not categories:
        return {}, min_score
    hints: dict[int, dict] = {}
    per_category = Counter()
    for category in categories:
        weights = Counter()
        for token in _tokenize_text(category.name):
            weights[token] += name_weight
        hints[category.id] = {"category": category, "weights": weights}
    query = (
        Product.objects.filter(category_id__in=[cat.id for cat in categories])
        .select_related("brand")
        .values(
            "category_id",
            "name",
            "description",
            "sku",
            "manufacturer_reference",
            "barcode",
            "brand__name",
        )
        .order_by("-updated_at")
    )
    total = 0
    for row in query.iterator():
        if max_products and total >= max_products:
            break
        category_id = row.get("category_id")
        if category_id not in hints:
            continue
        if max_per_category and per_category[category_id] >= max_per_category:
            continue
        text = _build_hint_text(row)
        tokens = _tokenize_text(text)
        if not tokens:
            continue
        per_category[category_id] += 1
        total += 1
        weights = hints[category_id]["weights"]
        for token in tokens:
            weights[token] += 1
    for hint in hints.values():
        weights = hint["weights"]
        if max_tokens and len(weights) > max_tokens:
            hint["weights"] = Counter(dict(weights.most_common(max_tokens)))
    return hints, min_score


def _data_driven_category(
    product: Product,
    hints: dict[int, dict],
    min_score: int,
) -> Category | None:
    if not hints:
        return None
    text = _build_match_text(product)
    tokens = _tokenize_text(text)
    if not tokens:
        return None
    best_category = None
    best_score = 0
    token_set = set(tokens)
    for hint in hints.values():
        weights = hint["weights"]
        score = 0
        for token in token_set:
            weight = weights.get(token)
            if weight:
                score += weight
        if score > best_score:
            best_score = score
            best_category = hint["category"]
    if best_score < min_score:
        return None
    return best_category


def _brand_override_category(
    product: Product,
    categories: list[Category],
) -> Category | None:
    if not categories:
        return None
    raw_text = _build_match_text(product)
    normalized_text = _normalize(raw_text)
    brand = _detect_brand(product, normalized_text)
    if not brand:
        return None
    raw_upper = raw_text.upper()
    door_patterns = BRAND_DOOR_STATION_PATTERNS.get(brand, ())
    if _matches_patterns(raw_upper, door_patterns) or _has_any_keyword(
        normalized_text, DOOR_STATION_KEYWORDS
    ):
        return _category_for_door_station(categories)
    access_patterns = BRAND_ACCESS_CONTROL_PATTERNS.get(brand, ())
    if _matches_patterns(raw_upper, access_patterns) or _has_any_keyword(
        normalized_text, ACCESS_CONTROL_KEYWORDS
    ):
        return _category_for_access_control(categories)
    alarm_patterns = BRAND_ANTI_INTRUSION_PATTERNS.get(brand, ())
    if _matches_patterns(raw_upper, alarm_patterns) or _has_any_keyword(
        normalized_text, ANTI_INTRUSION_KEYWORDS
    ):
        return _category_for_anti_intrusion(categories)
    video_patterns = BRAND_VIDEO_SURVEILLANCE_PATTERNS.get(brand, ())
    if _matches_patterns(raw_upper, video_patterns) or _has_any_keyword(
        normalized_text, VIDEO_SURVEILLANCE_KEYWORDS
    ):
        return _category_for_video_surveillance(categories)
    if _has_any_keyword(normalized_text, ACCESSORY_KEYWORDS):
        return _category_for_accessory(categories)
    return None


def _detect_brand(product: Product, normalized_text: str) -> str | None:
    brand_value = str(getattr(product, "brand", "") or "").lower()
    for key, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            if alias in brand_value or alias in normalized_text:
                return key
    return None


def _matches_patterns(text: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _has_any_keyword(normalized_text: str, keywords: Iterable[str]) -> bool:
    for keyword in keywords:
        if _normalize(keyword) in normalized_text:
            return True
    return False


def _category_for_door_station(categories: list[Category]) -> Category | None:
    combos = [
        (("platine", "rue"), ("visiophonie", "interphone", "portier")),
        (("platine",), ("rue", "visiophonie", "interphone", "portier")),
        (("visiophonie",), ("platine", "rue", "interphone", "portier")),
        (("interphone",), ("platine", "rue", "visiophonie", "portier")),
        (("door", "station"), ("visiophonie", "interphone", "platine")),
    ]
    return _best_category_by_tokens(categories, combos)


def _category_for_anti_intrusion(categories: list[Category]) -> Category | None:
    combos = [
        (("anti", "intrusion"), ("alarme", "securite")),
        (("intrusion",), ("anti", "alarme", "securite")),
        (("alarme",), ("intrusion", "anti")),
        (("alarm",), ("intrusion", "anti")),
    ]
    return _best_category_by_tokens(categories, combos)


def _category_for_access_control(categories: list[Category]) -> Category | None:
    combos = [
        (("controle", "acces"), ("access", "control", "badge", "lecteur")),
        (("access", "control"), ("controle", "acces", "badge", "lecteur")),
        (("badge",), ("controle", "acces", "lecteur")),
        (("lecteur",), ("badge", "controle", "acces")),
        (("biometrique",), ("controle", "acces", "badge")),
    ]
    return _best_category_by_tokens(categories, combos)


def _category_for_video_surveillance(categories: list[Category]) -> Category | None:
    combos = [
        (("videosurveillance",), ("video", "camera", "cctv")),
        (("video", "surveillance"), ("camera", "cctv")),
        (("camera",), ("video", "surveillance", "cctv")),
        (("cctv",), ("video", "surveillance", "camera")),
        (("enregistreur",), ("video", "surveillance", "nvr", "dvr", "xvr")),
    ]
    return _best_category_by_tokens(categories, combos)


def _category_for_accessory(categories: list[Category]) -> Category | None:
    combos = [
        (("accessoire",), ("accessoires", "support", "fixation")),
        (("accessoires",), ("accessoire", "support", "fixation")),
        (("support",), ("accessoire", "accessoires")),
    ]
    return _best_category_by_tokens(categories, combos)


def _best_category_by_tokens(
    categories: list[Category],
    combos: list[tuple[tuple[str, ...], tuple[str, ...]]],
) -> Category | None:
    best = None
    best_score = 0
    for required_tokens, preferred_tokens in combos:
        for category in categories:
            normalized_name = _normalize(category.name)
            if any(token not in normalized_name for token in required_tokens):
                continue
            score = len(required_tokens) * 5
            for token in preferred_tokens:
                if token in normalized_name:
                    score += 1
            if score > best_score:
                best_score = score
                best = category
        if best:
            return best
    return best


def _ai_pick_category(
    generator: MistralTextGenerator,
    product: Product,
    categories: list[Category],
) -> str | None:
    if not categories:
        return None
    max_candidates = int(getattr(settings, "CATEGORY_AI_MAX_CANDIDATES", 80))
    candidates = _rank_categories(product, categories, max_candidates)
    if not candidates:
        return None
    prompt = _build_ai_prompt(product, candidates)
    response = generator.generate_text(
        prompt,
        temperature=float(getattr(settings, "CATEGORY_AI_TEMPERATURE", 0.2)),
        max_tokens=int(getattr(settings, "CATEGORY_AI_MAX_TOKENS", 120)),
    )
    if not response:
        return None
    return _parse_ai_response(response, candidates)


def _rank_categories(
    product: Product, categories: list[Category], max_candidates: int
) -> list[str]:
    raw_text = _build_match_text(product)
    normalized_text = _normalize(raw_text)
    tokens = set(normalized_text.split())
    scored = []
    for category in categories:
        normalized = _normalize(category.name)
        if not normalized:
            continue
        score = 0
        if normalized and normalized in normalized_text:
            score += len(normalized) * 2
        for token in normalized.split():
            if token in tokens:
                score += len(token)
        scored.append((score, category.name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if max_candidates <= 0:
        max_candidates = len(scored)
    positive = [name for score, name in scored if score > 0]
    if positive:
        return positive[:max_candidates]
    return [name for _, name in scored[:max_candidates]]


def _build_ai_prompt(product: Product, candidates: list[str]) -> str:
    details = [
        f"Produit: {product.name}",
        f"SKU: {product.sku}",
    ]
    if product.manufacturer_reference:
        details.append(f"Reference fabricant: {product.manufacturer_reference}")
    if product.barcode:
        details.append(f"Code-barres: {product.barcode}")
    if brand := getattr(product, "brand", None):
        details.append(f"Marque: {brand}")
    if category := getattr(product, "category", None):
        details.append(f"Categorie actuelle: {category}")
    if description := (product.description or "").strip():
        details.append(f"Description: {_truncate(description, 240)}")
    category_block = "\n".join(f"- {name}" for name in candidates)
    return (
        "Tu es un assistant qui choisit la meilleure categorie pour un produit.\n"
        "Choisis une categorie uniquement dans la liste ci-dessous. "
        "Si aucune ne convient, reponds NONE.\n"
        "Reponds uniquement en JSON sur une seule ligne: {\"category\": \"...\"}.\n\n"
        + "\n".join(details)
        + "\n\nCategories disponibles:\n"
        + category_block
    )


def _parse_ai_response(response: str, candidates: list[str]) -> str | None:
    normalized_map = {_normalize(name): name for name in candidates}
    raw = response.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
    match = re.search(r'"category"\s*:\s*"([^"]+)"', raw, re.IGNORECASE)
    if match:
        raw = match.group(1)
    raw = raw.strip().strip('"').strip()
    if not raw:
        return None
    normalized = _normalize(raw)
    if normalized in {"none", "aucun", "aucune", "sans correspondance", "no match"}:
        return None
    return normalized_map.get(normalized)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."
