import barcode_lookup


def test_primary_pharmacy_exact_barcode_wins(monkeypatch):
    barcode = "3337875797597"

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


def test_fallback_finds_real_pharmacy_product_and_rejects_noise(monkeypatch):
    barcode = "5022339752011"

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
