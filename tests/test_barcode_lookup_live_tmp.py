import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests


BARCODE = "3337875797597"
DOMAINS = [
    "pharmacy295.gr",
    "ofarmakopoiosmou.gr",
    "vita4you.gr",
    "tofarmakeiomou.gr",
    "pharmacydiscount.gr",
    "pharmacy2go.gr",
    "pharm24.gr",
    "pharmnet.gr",
    "mypharmacy.gr",
    "a-pharmacy.gr",
    "fotopharmacy.com",
    "youpharmacy.gr",
    "upharm.gr",
    "pharmacyline.gr",
    "thepharmacy.gr",
    "nicepharmacy.gr",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language": "el-GR,el;q=0.9,en;q=0.7",
}


def _strip(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


def _form_candidates(home_html, base_url):
    found = []
    for form in re.findall(r"<form\b.*?</form>", home_html, flags=re.I | re.S):
        text = _strip(form).lower()
        if "search" not in form.lower() and "αναζ" not in text:
            continue
        open_tag = re.search(r"<form\b[^>]*>", form, flags=re.I | re.S)
        if not open_tag:
            continue
        action_m = re.search(r'\baction\s*=\s*["\']([^"\']*)', open_tag.group(0), flags=re.I)
        method_m = re.search(r'\bmethod\s*=\s*["\']([^"\']*)', open_tag.group(0), flags=re.I)
        action = urljoin(base_url, html.unescape(action_m.group(1))) if action_m else base_url
        method = (method_m.group(1).upper() if method_m else "GET")
        names = re.findall(r'<input\b[^>]*\bname\s*=\s*["\']([^"\']+)', form, flags=re.I | re.S)
        preferred = next((n for n in names if n.lower() in {"q", "s", "search", "query", "search_query", "searchterm", "keyword", "text"}), "")
        if preferred:
            found.append((action, method, preferred))
    return found


def probe(domain):
    base = f"https://{domain}/"
    report = {"domain": domain, "home": None, "forms": [], "tries": []}
    try:
        home = requests.get(base, headers=HEADERS, timeout=5, allow_redirects=True)
        report["home"] = home.status_code
        forms = _form_candidates(home.text, home.url)
        report["forms"] = forms[:4]
    except Exception as exc:
        report["home_error"] = repr(exc)
        forms = []

    candidates = []
    candidates.extend(forms[:2])
    candidates.extend([
        (urljoin(base, "search"), "GET", "q"),
        (urljoin(base, "search"), "GET", "s"),
        (urljoin(base, "catalogsearch/result/"), "GET", "q"),
        (base, "GET", "s"),
    ])
    seen = set()
    for action, method, field in candidates:
        key = (action, method, field)
        if key in seen or method != "GET":
            continue
        seen.add(key)
        try:
            r = requests.get(action, params={field: BARCODE}, headers=HEADERS, timeout=5, allow_redirects=True)
            text = r.text
            report["tries"].append({
                "action": action,
                "field": field,
                "status": r.status_code,
                "final": r.url,
                "chars": len(text),
                "barcode": BARCODE in text,
                "anthelios": "anthelios" in text.lower(),
                "uvmune": "uvmune" in text.lower(),
                "blocked": bool(re.search(r"captcha|access denied|cloudflare|robot check", text, re.I)),
                "title": _strip((re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S) or ["", ""])[1])[:160],
            })
            if "anthelios" in text.lower() or "uvmune" in text.lower():
                break
        except Exception as exc:
            report["tries"].append({"action": action, "field": field, "error": repr(exc)})
    return report


def test_live_greek_provider_probe():
    reports = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(probe, domain) for domain in DOMAINS]
        for future in as_completed(futures):
            reports.append(future.result())

    # UPCItemDB is tested separately as a global barcode fallback candidate.
    try:
        u = requests.get(f"https://www.upcitemdb.com/upc/{BARCODE}", headers=HEADERS, timeout=8)
        reports.append({
            "domain": "upcitemdb.com",
            "status": u.status_code,
            "chars": len(u.text),
            "barcode": BARCODE in u.text,
            "anthelios": "anthelios" in u.text.lower(),
            "title": _strip((re.search(r"<title[^>]*>(.*?)</title>", u.text, re.I | re.S) or ["", ""])[1])[:200],
        })
    except Exception as exc:
        reports.append({"domain": "upcitemdb.com", "error": repr(exc)})

    raise AssertionError(sorted(reports, key=lambda x: x.get("domain", "")))
