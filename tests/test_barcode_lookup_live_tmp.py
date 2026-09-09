import re

import requests


def test_live_search_engine_diagnostic():
    barcode = "3337875797597"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept-Language": "el-GR,el;q=0.9,en;q=0.7",
    }
    targets = [
        ("ddg_html", "https://html.duckduckgo.com/html/", {"q": f'"{barcode}" pharmacy'}),
        ("bing", "https://www.bing.com/search", {"q": f'"{barcode}" pharmacy'}),
        ("google", "https://www.google.com/search", {"q": f'"{barcode}" pharmacy'}),
    ]
    report = []
    for name, url, params in targets:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10, allow_redirects=True)
            text = response.text
            report.append(
                {
                    "name": name,
                    "status": response.status_code,
                    "final_url": response.url,
                    "chars": len(text),
                    "has_barcode": barcode in text,
                    "has_anthelios": "anthelios" in text.lower(),
                    "blocked": bool(re.search(r"captcha|unusual traffic|robot|access denied", text, re.I)),
                    "sample": re.sub(r"\s+", " ", text[:500]),
                }
            )
        except Exception as exc:
            report.append({"name": name, "error": repr(exc)})

    raise AssertionError(report)
