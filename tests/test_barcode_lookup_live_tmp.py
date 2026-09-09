import barcode_lookup


def test_live_lookup_two_known_real_barcodes():
    cases = [
        ("3337875797597", "ANTHELIOS"),
        ("5022339752011", "QUEST"),
    ]

    failures = []
    for barcode, expected_token in cases:
        results = barcode_lookup.lookup_barcode_online(barcode)
        print(f"LIVE_LOOKUP {barcode}: {results}")
        if not results:
            failures.append(f"{barcode}: no candidates")
            continue
        names = " | ".join(str(item.get("product_name", "")) for item in results).upper()
        if expected_token not in names:
            failures.append(f"{barcode}: expected token {expected_token!r} not in {names!r}")

    assert not failures, "; ".join(failures)
