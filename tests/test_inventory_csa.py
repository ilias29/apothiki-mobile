import pandas as pd

import app_inventory_search as core
import inventory_csa as csa


def _receipt(*, qty, lot, expiry):
    return core.make_transaction(
        code_type="Barcode",
        code_value="5201234567890",
        barcode="5201234567890",
        brand="TEST BRAND",
        product="TEST PRODUCT",
        category="Φάρμακο",
        location_id=0,
        movement="Παραλαβή (+)",
        quantity=qty,
        delta=qty,
        lot_number=lot,
        expiry_date=expiry,
        transaction_id=f"in-{lot}",
    )


def _stock():
    data = core.records_to_dataframe([
        _receipt(qty=2, lot="EARLY", expiry="2026-10-31"),
        _receipt(qty=3, lot="LATE", expiry="2027-05-31"),
    ])
    snapshot = csa.stock_snapshot(data)
    return snapshot, csa.product_summary(snapshot)


def test_fefo_issues_earliest_lot_first():
    snapshot, summary = _stock()
    txs = csa.fefo_issue_transactions(
        snapshot,
        summary.iloc[0],
        quantity=4,
        note="test",
        transaction_prefix="out-test",
    )
    assert [(tx["LotNumber"], tx["DeltaQty"]) for tx in txs] == [
        ("EARLY", -2),
        ("LATE", -2),
    ]


def test_ai_exit_aggregates_same_product_before_fefo():
    snapshot, summary = _stock()
    rows = pd.DataFrame([
        {"ProductName": "TEST PRODUCT", "Brand": "TEST BRAND", "BarcodeOrGTIN": "5201234567890", "GTIN": "", "Strength": "", "Quantity": 1, "Notes": ""},
        {"ProductName": "TEST PRODUCT", "Brand": "TEST BRAND", "BarcodeOrGTIN": "5201234567890", "GTIN": "", "Strength": "", "Quantity": 2, "Notes": ""},
    ])
    txs, errors = csa.ai_issue_transactions(snapshot, summary, rows)
    assert errors == []
    assert sum(tx["DeltaQty"] for tx in txs) == -3
    assert txs[0]["LotNumber"] == "EARLY"


def test_document_reference_prevents_duplicate_seed_after_rephoto():
    a = csa.batch_seed([b"first-photo"], "SUPPLIER|INV-100|2026-09-02")
    b = csa.batch_seed([b"different-photo"], "SUPPLIER|INV-100|2026-09-02")
    assert a == b


def test_ai_normalization_keeps_identifier_as_text_and_quantity_integer():
    frame = csa.normalize_ai_items([
        {
            "ProductName": "Test",
            "Brand": "Brand",
            "BarcodeOrGTIN": "05201234567890",
            "GTIN": "",
            "Category": "Φάρμακο",
            "Quantity": 4,
            "NetPrice": None,
            "VATRate": None,
            "GrossPrice": None,
            "ExpiryDate": "",
            "LotNumber": "",
            "SerialNumber": "",
            "Strength": "",
            "DosageForm": "",
            "Confidence": "high",
            "Notes": "",
        }
    ])
    assert frame.loc[0, "BarcodeOrGTIN"] == "05201234567890"
    assert int(frame.loc[0, "Quantity"]) == 4
