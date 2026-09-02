import pandas as pd
import pytest

import app_inventory_search as core
import inventory_csa as csa


def _receipt(*, qty, lot, expiry, location_id=0, serial="", transaction_id=None):
    return core.make_transaction(
        code_type="Barcode",
        code_value="5201234567890",
        barcode="5201234567890",
        brand="TEST BRAND",
        product="TEST PRODUCT",
        category="Φάρμακο",
        location_id=location_id,
        movement="Παραλαβή (+)",
        quantity=qty,
        delta=qty,
        lot_number=lot,
        expiry_date=expiry,
        serial_number=serial,
        transaction_id=transaction_id or f"in-{location_id}-{lot}-{serial or 'bulk'}",
    )


def _stock():
    data = core.records_to_dataframe([
        _receipt(qty=2, lot="EARLY", expiry="2098-10-31"),
        _receipt(qty=3, lot="LATE", expiry="2099-05-31"),
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


def test_hard_fefo_never_issues_expired_stock_and_uses_no_expiry_last():
    today = pd.Timestamp.now().normalize()
    expired = (today - pd.Timedelta(days=10)).date().isoformat()
    soon = (today + pd.Timedelta(days=20)).date().isoformat()
    later = (today + pd.Timedelta(days=200)).date().isoformat()
    data = core.records_to_dataframe([
        _receipt(qty=5, lot="EXPIRED", expiry=expired),
        _receipt(qty=1, lot="SOON", expiry=soon),
        _receipt(qty=2, lot="LATER", expiry=later),
        _receipt(qty=4, lot="NOEXP", expiry=""),
    ])
    snapshot = csa.stock_snapshot(data)
    summary = csa.product_summary(snapshot)
    txs = csa.fefo_issue_transactions(
        snapshot,
        summary.iloc[0],
        quantity=4,
        note="hard-test",
        transaction_prefix="hard-fefo",
    )
    assert [(tx["LotNumber"], tx["DeltaQty"]) for tx in txs] == [
        ("SOON", -1),
        ("LATER", -2),
        ("NOEXP", -1),
    ]
    assert all(tx["LotNumber"] != "EXPIRED" for tx in txs)


def test_hard_fefo_rejects_request_when_only_expired_stock_would_cover_it():
    today = pd.Timestamp.now().normalize()
    expired = (today - pd.Timedelta(days=1)).date().isoformat()
    valid = (today + pd.Timedelta(days=30)).date().isoformat()
    data = core.records_to_dataframe([
        _receipt(qty=20, lot="EXPIRED", expiry=expired),
        _receipt(qty=2, lot="VALID", expiry=valid),
    ])
    snapshot = csa.stock_snapshot(data)
    summary = csa.product_summary(snapshot)
    with pytest.raises(core.InventoryError, match="μη ληγμένο stock"):
        csa.fefo_issue_transactions(
            snapshot,
            summary.iloc[0],
            quantity=3,
            note="hard-test",
            transaction_prefix="hard-reject",
        )


def test_hard_ai_exit_refuses_ambiguous_same_barcode_across_locations():
    future = (pd.Timestamp.now().normalize() + pd.Timedelta(days=365)).date().isoformat()
    data = core.records_to_dataframe([
        _receipt(qty=2, lot="UP", expiry=future, location_id=2),
        _receipt(qty=3, lot="STORE", expiry=future, location_id=0),
    ])
    snapshot = csa.stock_snapshot(data)
    summary = csa.product_summary(snapshot)
    rows = pd.DataFrame([
        {
            "ProductName": "TEST PRODUCT",
            "Brand": "TEST BRAND",
            "BarcodeOrGTIN": "5201234567890",
            "GTIN": "",
            "Strength": "",
            "Quantity": 1,
            "Notes": "",
        }
    ])
    txs, errors = csa.ai_issue_transactions(snapshot, summary, rows)
    assert txs == []
    assert len(errors) == 1
    assert "Αμφίβολη αντιστοίχιση" in errors[0]


def test_hard_serialized_receipt_rejects_aggregate_quantity():
    frame = csa.normalize_ai_items([
        {
            "ProductName": "SERIAL DRUG",
            "Brand": "TEST",
            "BarcodeOrGTIN": "05201234567890",
            "GTIN": "05201234567890",
            "Category": "Φάρμακο",
            "Quantity": 2,
            "NetPrice": None,
            "VATRate": None,
            "GrossPrice": None,
            "ExpiryDate": "2099-12-31",
            "LotNumber": "LOT-S",
            "SerialNumber": "SN-ONE-PACK",
            "Strength": "100MG",
            "DosageForm": "TAB",
            "Confidence": "high",
            "Notes": "",
        }
    ])
    with pytest.raises(ValueError, match="SN, άρα Quantity=1"):
        csa.receipt_transaction(
            frame.iloc[0],
            location_id=0,
            transaction_id="serial-hard-test",
            source_note="source=test",
        )
