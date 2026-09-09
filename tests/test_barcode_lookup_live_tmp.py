import html
import re

import requests


def _strip(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


def test_live_search_engine_diagnostic():
    barcode = "3337875797597"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept-Language": "el-GR,el;q=0.9,en;q=0.7",
    }
    queries = [
        f'"{barcode}"',
        f'"{barcode}" site:pharmnet.gr',
        f'"{barcode}" site:mypharmacy.gr',
        f'"{barcode}" site:pharmacyline.gr',
    ]
    report = []
    for query in queries:
        response = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "setlang": "el"},
            headers=headers,
            timeout=10,
            allow_redirects=True,
        )
        text = response.text
        blocks = re.findall(r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', text, re.I | re.S)
        parsed = []
        for block in blocks[:8]:
            m = re.search(r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
            p = re.search(r'<p[^>]*>(.*?)</p>', block, re.I | re.S)
            if m:
                parsed.append({"url": html.unescape(m.group(1)), "title": _strip(m.group(2)), "snippet": _strip(p.group(1)) if p else ""})
        report.append(
            {
                "query": query,
                "status": response.status_code,
                "chars": len(text),
                "has_barcode": barcode in text,
                "has_anthelios": "anthelios" in text.lower(),
                "blocks": len(blocks),
                "parsed": parsed,
            }
        )

    raise AssertionError(report)
