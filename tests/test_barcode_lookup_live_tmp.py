import app_inventory_search as core


def test_live_provider_parser_diagnostic():
    barcode = "3337875797597"
    sources = [
        ("pharmacy295.gr", f"https://www.pharmacy295.gr/catalog?q={barcode}"),
        ("pharm24.gr", f"https://www.pharm24.gr/search?q={barcode}"),
        ("nicepharmacy.gr", f"https://www.nicepharmacy.gr/search?q={barcode}"),
        ("pharmacyline.gr", f"https://www.pharmacyline.gr/el/search?search_query={barcode}"),
    ]
    report = []
    for domain, url in sources:
        found, debug = core._lookup_greek_provider(domain, url, barcode)
        report.append({
            "domain": domain,
            "found": found,
            "search_status": debug.get("search_status"),
            "error": debug.get("error"),
            "rejection_reason": debug.get("rejection_reason"),
            "candidate_detail_urls": debug.get("candidate_detail_urls"),
            "detail_pages_inspected": debug.get("detail_pages_inspected"),
        })
    raise AssertionError(report)
