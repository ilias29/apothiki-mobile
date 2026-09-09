import html
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests


SEARCH_TIMEOUT = 8
MAX_RESULTS = 8

# Κύριες online πηγές για barcode lookup. Πρώτα επιχειρούμε άμεση,
# επιβεβαιωμένη αναζήτηση σε pharmacy e-shops και μόνο αν δεν βρεθεί
# exact barcode κάνουμε γενικό web fallback.
PRIMARY_PHARMACY_DOMAINS = [
    "pharmacy295.gr",
    "ofarmakopoiosmou.gr",
]

FALLBACK_DOMAINS = [
    "vita4you.gr",
    "tofarmakeiomou.gr",
    "skroutz.gr",
    "bestprice.gr",
    "galinos.gr",
]

PREFERRED_DOMAINS = PRIMARY_PHARMACY_DOMAINS + FALLBACK_DOMAINS


def clean(value: Any) -> str:
    return str(value or "").strip()


def _unwrap_ddg_url(url: str) -> str:
    url = html.unescape(clean(url))
    if not url:
        return ""
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    if url.startswith("//"):
        return "https:" + url
    return url


def _strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _clean_title(title: str, barcode: str) -> str:
    title = _strip_tags(title)
    title = re.sub(
        r"\s*[-|·]\s*(Skroutz|BestPrice|φαρμακείο|pharmacy).*?$",
        "",
        title,
        flags=re.I,
    )
    title = title.replace(barcode, "").strip(" -|·")
    return title.strip()


def _looks_like_product(title: str, snippet: str, barcode: str) -> bool:
    text = f"{title} {snippet}".lower()
    if not title or len(title) < 4:
        return False
    blocked = [
        "login",
        "σύνδεση",
        "καλάθι",
        "privacy",
        "όροι χρήσης",
        "λογαριασμός",
        "contact",
        "επικοινωνία",
    ]
    if any(token in text for token in blocked):
        return False
    return barcode in text or any(
        token in text
        for token in [
            "mg",
            "ml",
            "spf",
            "caps",
            "tabs",
            "δισκ",
            "κάψ",
            "κρέμα",
            "cream",
            "serum",
            "spray",
            "gel",
            "shampoo",
            "σαμπουάν",
            "vitamin",
            "βιταμ",
        ]
    )


def _search_ddg(query: str) -> list[dict[str, str]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PharmacyInventory/1.0)",
        "Accept-Language": "el-GR,el;q=0.9,en;q=0.7",
    }
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=headers,
        timeout=SEARCH_TIMEOUT,
    )
    response.raise_for_status()
    text = response.text

    links = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        text,
        flags=re.I | re.S,
    )
    snippets = re.findall(
        r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
        text,
        flags=re.I | re.S,
    )

    results = []
    for index, (raw_url, raw_title) in enumerate(links[: MAX_RESULTS * 2]):
        url = _unwrap_ddg_url(raw_url)
        title = _strip_tags(raw_title)
        snippet = _strip_tags(snippets[index]) if index < len(snippets) else ""
        if url and title:
            results.append({"title": title, "snippet": snippet, "url": url})
    return results


def _search_queries(barcode: str) -> list[str]:
    # Πρώτα ξεχωριστό exact-barcode search για κάθε κύριο pharmacy e-shop.
    queries = [f'"{barcode}" site:{domain}' for domain in PRIMARY_PHARMACY_DOMAINS]

    # Μετά fallback σε ευρύτερο ελληνικό pharmacy/product search.
    fallback_domain_query = " OR ".join(f"site:{domain}" for domain in FALLBACK_DOMAINS)
    queries.append(f'"{barcode}" ({fallback_domain_query})')
    queries.append(f'"{barcode}"')
    return queries


def _candidate_key(candidate: dict[str, Any]) -> str:
    name = re.sub(r"\W+", " ", clean(candidate.get("product_name")).lower()).strip()
    return name or clean(candidate.get("url")).lower()


def _verified_provider_candidates(barcode: str) -> list[dict[str, Any]]:
    """Try direct provider pages and return only exact-barcode verified products.

    app_inventory_search already contains the stricter provider parser: it opens
    the provider search page, follows product detail pages and marks a result as
    verified only when the requested barcode/GTIN is present on the product page.
    Keeping that verification ahead of search-engine snippets sharply reduces
    false positives while still allowing the looser web search as fallback.
    """
    try:
        import app_inventory_search as inventory_search

        found, _debug = inventory_search.online_lookup_candidates(barcode, "")
    except Exception:
        return []

    candidates: list[dict[str, Any]] = []
    seen = set()
    is_gtin14 = barcode.isdigit() and len(barcode) == 14
    for item in found or []:
        if not item.get("verified"):
            continue
        product_name = clean(item.get("product_name"))
        if not product_name:
            continue
        candidate = {
            "product_name": product_name,
            "brand": clean(item.get("brand")),
            "barcode": "" if is_gtin14 else barcode,
            "gtin": barcode if is_gtin14 else "",
            "strength": clean(item.get("strength")),
            "dosage_form": clean(item.get("dosage_form")),
            "category": clean(item.get("category")) or "Άλλο",
            "source": clean(item.get("provider")) or "verified pharmacy",
            "url": clean(item.get("product_page_url")),
            "confidence": 0.99,
            "verified": True,
        }
        key = _candidate_key(candidate)
        if key and key not in seen:
            seen.add(key)
            candidates.append(candidate)
        if len(candidates) >= 5:
            break
    return candidates


def _fallback_search_candidates(barcode: str) -> list[dict[str, Any]]:
    raw_results: list[dict[str, str]] = []
    seen_urls = set()
    for query in _search_queries(barcode):
        try:
            for result in _search_ddg(query):
                if result["url"] not in seen_urls:
                    seen_urls.add(result["url"])
                    raw_results.append(result)
        except Exception:
            # Ένα provider/search failure δεν πρέπει να ρίχνει ολόκληρο το lookup.
            continue

    candidates = []
    for result in raw_results:
        title = _clean_title(result["title"], barcode)
        snippet = result["snippet"]
        if not _looks_like_product(title, snippet, barcode):
            continue

        domain = _domain(result["url"])
        exact_barcode_visible = barcode in f"{result['title']} {snippet}"
        primary = any(domain.endswith(item) for item in PRIMARY_PHARMACY_DOMAINS)
        fallback = any(domain.endswith(item) for item in FALLBACK_DOMAINS)

        confidence = 0.80 if exact_barcode_visible else 0.60
        if primary:
            confidence += 0.15
        elif fallback:
            confidence += 0.08

        candidates.append(
            {
                "product_name": title,
                "brand": "",
                "barcode": barcode,
                "gtin": "",
                "strength": "",
                "dosage_form": "",
                "category": "Άλλο",
                "source": domain or "web",
                "url": result["url"],
                "confidence": min(confidence, 0.98),
                "verified": False,
            }
        )

    deduped = []
    seen = set()
    for candidate in sorted(candidates, key=lambda item: item["confidence"], reverse=True):
        key = _candidate_key(candidate)
        if key and key not in seen:
            seen.add(key)
            deduped.append(candidate)
        if len(deduped) >= 5:
            break
    return deduped


def lookup_barcode_online(barcode: str) -> list[dict[str, Any]]:
    barcode = re.sub(r"\s+", "", clean(barcode))
    if not barcode:
        return []

    # 1) Σοβαρή πηγή: exact barcode/GTIN επιβεβαιωμένο μέσα σε product detail page.
    verified = _verified_provider_candidates(barcode)
    if verified:
        return verified

    # 2) Αν δεν υπάρχει verified detail page, search-engine discovery.
    # Παραμένει υποψήφιο αποτέλεσμα και απαιτεί πάντα ανθρώπινο OK στο app.
    return _fallback_search_candidates(barcode)
