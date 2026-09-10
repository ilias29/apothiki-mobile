import urllib.parse

import app_inventory_search
import barcode_lookup


def test_html_search_layout_parses_real_full_health_barcode_result():
    barcode = "5200421900551"
    html = f'''<div class="result">
      <a class="result__a" href="https://pharmasee.gr/product/full-health-zinc-chelate-plus-copper-100-vcaps/">
        Full Health Zinc Chelate Plus Copper 100 Vcaps
      </a><div class="result__snippet">Κωδικός προϊόντος: {barcode}</div>
    </div>'''
    results = barcode_lookup._parse_ddg_results(html)
    assert results[0]["title"] == "Full Health Zinc Chelate Plus Copper 100 Vcaps"
    assert barcode in results[0]["snippet"]


def test_lite_search_layout_parses_real_solgar_barcode_result():
    barcode = "033984003972"
    html = f'''<table><tr><td>
      <a rel="nofollow" class="result-link" href="https://example-pharmacy.gr/solgar-b12">
        Solgar Methylcobalamin B12 1000 μg 30 Nuggets
      </a></td></tr><tr><td class="result-snippet">UPC {barcode}</td></tr></table>'''
    results = barcode_lookup._parse_ddg_results(html)
    assert "Solgar Methylcobalamin" in results[0]["title"]
    assert barcode in results[0]["snippet"]


def test_search_retries_with_lite_layout_when_cloud_html_is_blocked(monkeypatch):
    calls = []

    class Response:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        if "html.duckduckgo.com" in url:
            return Response("anomaly-modal robot check")
        return Response('<a class="result-link" href="https://shop.gr/product">Product 1000 mg</a>')

    monkeypatch.setattr(barcode_lookup.requests, "request", fake_request)
    results = barcode_lookup._search_ddg('"033984003972"')
    assert results[0]["title"] == "Product 1000 mg"
    assert [method for method, _url in calls] == ["post", "get"]


def test_primary_pharmacy_exact_barcode_wins(monkeypatch):
    barcode = "3337875797597"
    monkeypatch.setattr(barcode_lookup, "_verified_provider_candidates", lambda _code: [])

    def fake_search(query):
        if "site:pharmacy295.gr" in query:
            return [
                {
                    "title": "La Roche-Posay Anthelios UVMune 400 Invisible Fluid SPF50+ 50ml",
                    "snippet": f"Barcode: {barcode}",
                    "url": "https://www.pharmacy295.gr/la-roche-posay-anthelios-uvmune-400",
                }
            ]
        if "site:ofarmakopoiosmou.gr" in query:
            return []
        return [
            {
                "title": "La Roche-Posay Anthelios UVMune 400 Invisible Fluid SPF50+ 50ml",
                "snippet": f"EAN {barcode}",
                "url": "https://www.vita4you.gr/example-anthelios",
            }
        ]

    monkeypatch.setattr(barcode_lookup, "_search_ddg", fake_search)
    results = barcode_lookup.lookup_barcode_online(barcode)

    assert results
    assert results[0]["source"] == "pharmacy295.gr"
    assert results[0]["barcode"] == barcode
    assert "Anthelios" in results[0]["product_name"]
    assert results[0]["confidence"] >= 0.95
    assert results[0]["verified"] is False


def test_fallback_finds_real_pharmacy_product_and_rejects_noise(monkeypatch):
    barcode = "5022339752011"
    monkeypatch.setattr(barcode_lookup, "_verified_provider_candidates", lambda _code: [])

    def fake_search(query):
        if "site:pharmacy295.gr" in query or "site:ofarmakopoiosmou.gr" in query:
            return []
        return [
            {
                "title": "Login - Pharmacy",
                "snippet": "Σύνδεση στο λογαριασμό",
                "url": "https://example.gr/login",
            },
            {
                "title": "Quest Vitamin D3 2500iu 60 tabs",
                "snippet": f"Barcode: {barcode} συμπλήρωμα διατροφής",
                "url": "https://www.vita4you.gr/quest-vitamin-d3-2500iu-60tabs",
            },
        ]

    monkeypatch.setattr(barcode_lookup, "_search_ddg", fake_search)
    results = barcode_lookup.lookup_barcode_online(barcode)

    assert len(results) == 1
    assert results[0]["source"] == "vita4you.gr"
    assert results[0]["barcode"] == barcode
    assert "Quest Vitamin D3" in results[0]["product_name"]
    assert "login" not in results[0]["product_name"].lower()


def test_verified_direct_result_short_circuits_search_engine(monkeypatch):
    barcode = "3337875797597"
    verified = [
        {
            "product_name": "LA ROCHE-POSAY ANTHELIOS UVMUNE 400 50ML",
            "brand": "LA ROCHE-POSAY",
            "barcode": barcode,
            "gtin": "",
            "strength": "",
            "dosage_form": "FLUID",
            "category": "Άλλο",
            "source": "pharmacy295.gr",
            "url": "https://www.pharmacy295.gr/product/example",
            "confidence": 0.99,
            "verified": True,
        }
    ]
    monkeypatch.setattr(barcode_lookup, "_verified_provider_candidates", lambda _code: verified)

    def should_not_run(_query):
        raise AssertionError("DDG fallback should not run when an exact provider page is verified")

    monkeypatch.setattr(barcode_lookup, "_search_ddg", should_not_run)
    results = barcode_lookup.lookup_barcode_online(barcode)

    assert results == verified
    assert results[0]["verified"] is True
    assert results[0]["confidence"] == 0.99


def test_direct_provider_adapter_keeps_only_exact_verified_results(monkeypatch):
    barcode = "5022339752011"

    monkeypatch.setattr(
        app_inventory_search,
        "online_lookup_candidates",
        lambda code, product_name="": (
            [
                {
                    "product_name": "QUEST VITAMIN D3 2500IU 60 TABS",
                    "brand": "QUEST",
                    "strength": "2500IU",
                    "dosage_form": "60 TABS",
                    "provider": "pharmacy295.gr",
                    "product_page_url": "https://www.pharmacy295.gr/quest-d3",
                    "verified": True,
                },
                {
                    "product_name": "WRONG PRODUCT",
                    "provider": "discountpharmacy.gr",
                    "product_page_url": "https://www.discountpharmacy.gr/wrong",
                    "verified": False,
                },
            ],
            {"attempted": []},
        ),
    )

    results = barcode_lookup._verified_provider_candidates(barcode)

    assert len(results) == 1
    assert results[0]["product_name"] == "QUEST VITAMIN D3 2500IU 60 TABS"
    assert results[0]["source"] == "pharmacy295.gr"
    assert results[0]["barcode"] == barcode
    assert results[0]["verified"] is True
    assert results[0]["confidence"] == 0.99


def test_gtin14_verified_direct_result_is_stored_as_gtin(monkeypatch):
    gtin = "01234567890128"
    monkeypatch.setattr(
        app_inventory_search,
        "online_lookup_candidates",
        lambda code, product_name="": (
            [
                {
                    "product_name": "TEST PRODUCT",
                    "provider": "pharmacy295.gr",
                    "product_page_url": "https://www.pharmacy295.gr/test-product",
                    "verified": True,
                }
            ],
            {},
        ),
    )

    results = barcode_lookup._verified_provider_candidates(gtin)

    assert len(results) == 1
    assert results[0]["barcode"] == ""
    assert results[0]["gtin"] == gtin


def test_unverified_direct_results_fall_back_to_web_discovery(monkeypatch):
    barcode = "5201234567890"
    monkeypatch.setattr(barcode_lookup, "_verified_provider_candidates", lambda _code: [])
    monkeypatch.setattr(
        barcode_lookup,
        "_search_ddg",
        lambda _query: [
            {
                "title": "Example Cream 50ml",
                "snippet": f"EAN {barcode}",
                "url": "https://www.vita4you.gr/example-cream",
            }
        ],
    )

    results = barcode_lookup.lookup_barcode_online(barcode)

    assert results
    assert results[0]["source"] == "vita4you.gr"
    assert results[0]["verified"] is False


def test_search_queries_are_ordered_primary_then_fallback_then_general():
    barcode = "3337875797597"
    queries = barcode_lookup._search_queries(barcode)

    assert queries[0] == f'"{barcode}" site:pharmacy295.gr'
    assert queries[1] == f'"{barcode}" site:ofarmakopoiosmou.gr'
    assert "vita4you.gr" in queries[2]
    assert queries[-1] == f'"{barcode}"'


def test_one_search_failure_does_not_abort_other_sources(monkeypatch):
    barcode = "3337875797597"
    monkeypatch.setattr(barcode_lookup, "_verified_provider_candidates", lambda _code: [])
    calls = {"count": 0}

    def flaky_search(query):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary search failure")
        if calls["count"] == 2:
            return [
                {
                    "title": "La Roche-Posay Anthelios Fluid SPF50+ 50ml",
                    "snippet": f"Barcode {barcode}",
                    "url": "https://www.ofarmakopoiosmou.gr/anthelios",
                }
            ]
        return []

    monkeypatch.setattr(barcode_lookup, "_search_ddg", flaky_search)
    results = barcode_lookup.lookup_barcode_online(barcode)

    assert calls["count"] == 4
    assert results
    assert results[0]["source"] == "ofarmakopoiosmou.gr"


def test_exact_fallback_can_beat_primary_result_without_barcode_evidence(monkeypatch):
    barcode = "5022339752011"
    monkeypatch.setattr(barcode_lookup, "_verified_provider_candidates", lambda _code: [])

    def fake_search(query):
        if "site:pharmacy295.gr" in query:
            return [
                {
                    "title": "Quest Vitamin D3 2500iu 60 tabs",
                    "snippet": "Vitamin supplement 60 tabs",
                    "url": "https://www.pharmacy295.gr/quest-d3",
                }
            ]
        if "site:ofarmakopoiosmou.gr" in query:
            return []
        if "vita4you.gr" in query:
            return [
                {
                    "title": "Quest Vitamin D3 2500iu 60 tabs",
                    "snippet": f"Barcode {barcode}",
                    "url": "https://www.vita4you.gr/quest-d3",
                }
            ]
        return []

    monkeypatch.setattr(barcode_lookup, "_search_ddg", fake_search)
    results = barcode_lookup.lookup_barcode_online(barcode)

    assert results
    assert results[0]["source"] == "vita4you.gr"
    assert results[0]["confidence"] > 0.80


def test_duplicate_product_titles_are_collapsed(monkeypatch):
    barcode = "3337875797597"
    monkeypatch.setattr(barcode_lookup, "_verified_provider_candidates", lambda _code: [])

    def fake_search(_query):
        return [
            {
                "title": "La Roche-Posay Anthelios UVMune 400 SPF50+ 50ml",
                "snippet": f"Barcode {barcode}",
                "url": "https://www.pharmacy295.gr/a",
            },
            {
                "title": "La Roche-Posay Anthelios UVMune 400 SPF50+ 50ml",
                "snippet": f"EAN {barcode}",
                "url": "https://www.vita4you.gr/a",
            },
        ]

    monkeypatch.setattr(barcode_lookup, "_search_ddg", fake_search)
    results = barcode_lookup.lookup_barcode_online(barcode)

    assert len(results) == 1
    assert results[0]["source"] == "pharmacy295.gr"


def test_empty_input_returns_without_any_network(monkeypatch):
    monkeypatch.setattr(
        barcode_lookup,
        "_verified_provider_candidates",
        lambda _code: (_ for _ in ()).throw(AssertionError("direct lookup should not run")),
    )
    monkeypatch.setattr(
        barcode_lookup,
        "_search_ddg",
        lambda _query: (_ for _ in ()).throw(AssertionError("web lookup should not run")),
    )

    assert barcode_lookup.lookup_barcode_online("   ") == []


def test_duckduckgo_redirect_url_is_unwrapped():
    target = "https://www.pharmacy295.gr/product/test?id=1"
    wrapped = "https://duckduckgo.com/l/?uddg=" + urllib.parse.quote(target, safe="")

    assert barcode_lookup._unwrap_ddg_url(wrapped) == target
