import html
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests


SEARCH_TIMEOUT = 8
MAX_RESULTS = 8

# Δημόσιες πηγές που έχουν συχνά φαρμακευτικά/παραφαρμακευτικά προϊόντα.
# Οι αποθήκες φαρμακοποιών συνήθως θέλουν login, οπότε χρησιμοποιούνται μόνο
# όταν το αποτέλεσμά τους είναι δημόσια προσβάσιμο από search engine.
PREFERRED_DOMAINS = [
    "skroutz.gr",
    "bestprice.gr",
    "ofarmakopoiosmou.gr",
    "vita4you.gr",
    "pharmacy295.gr",
    "tofarmakeiomou.gr",
    "galinos.gr",
]


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
    title = re.sub(r"\s*[-|·]\s*(Skroutz|BestPrice|φαρμακείο|pharmacy).*?$", "", title, flags=re.I)
    title = title.replace(barcode, "").strip(" -|·")
    return title.strip()


def _looks_like_product(title: str, snippet: str, barcode: str) -> bool:
    text = f"{title} {snippet}".lower()
    if not title or len(title) < 4:
        return False
    blocked = ["login", "σύνδεση", "καλάθι", "privacy", "όροι χρήσης"]
    if any(token in text for token in blocked):
        return False
    return barcode in text or any(
        token in text
        for token in ["mg", "ml", "spf", "caps", "tabs", "δισκ", "κάψ", "κρέμα", "serum", "spray"]
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
    for index, (raw_url, raw_title) in enumerate(links[:MAX_RESULTS * 2]):
        url = _unwrap_ddg_url(raw_url)
        title = _strip_tags(raw_title)
        snippet = _strip_tags(snippets[index]) if index < len(snippets) else ""
        if url and title:
            results.append({"title": title, "snippet": snippet, "url": url})
    return results


def lookup_barcode_online(barcode: str) -> list[dict[str, Any]]:
    barcode = re.sub(r"\s+", "", clean(barcode))
    if not barcode:
        return []

    queries = [f'"{barcode}"']
    domain_query = " OR ".join(f"site:{domain}" for domain in PREFERRED_DOMAINS[:5])
    queries.append(f'"{barcode}" ({domain_query})')

    raw_results: list[dict[str, str]] = []
    seen_urls = set()
    for query in queries:
        try:
            for result in _search_ddg(query):
                if result["url"] not in seen_urls:
                    seen_urls.add(result["url"])
                    raw_results.append(result)
        except Exception:
            continue

    candidates = []
    for result in raw_results:
        title = _clean_title(result["title"], barcode)
        snippet = result["snippet"]
        if not _looks_like_product(title, snippet, barcode):
            continue
        domain = _domain(result["url"])
        preferred = any(domain.endswith(item) for item in PREFERRED_DOMAINS)
        confidence = 0.80 if barcode in f"{result['title']} {snippet}" else 0.60
        if preferred:
            confidence += 0.10
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
                "confidence": min(confidence, 0.95),
            }
        )

    deduped = []
    seen_titles = set()
    for candidate in sorted(candidates, key=lambda item: item["confidence"], reverse=True):
        key = re.sub(r"\W+", " ", candidate["product_name"].lower()).strip()
        if key and key not in seen_titles:
            seen_titles.add(key)
            deduped.append(candidate)
        if len(deduped) >= 5:
            break
    return deduped
