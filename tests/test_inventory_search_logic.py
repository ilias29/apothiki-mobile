import pandas as pd
import pytest

import app_inventory_search as app


class FakeWorksheet:
    def __init__(self, headers=None, records=None):
        self.headers = list(headers or [])
        self.records = list(records or [])
        self.appended = []

    def row_values(self, row):
        return list(self.headers)

    def update(self, cell, values):
        assert cell == "A1"
        self.headers = list(values[0])

    def get_all_records(self):
        return list(self.records)

    def append_row(self, values, value_input_option=None):
        row = dict(zip(self.headers, values))
        self.records.append(row)
        self.appended.append(row)


def base_row(**overrides):
    row = app.make_transaction(
        code_type="Barcode",
        code_value="123",
        barcode="123",
        pc_code="",
        serial_number="",
        brand="Brand",
        product="Product",
        category="Φάρμακο",
        location_id=0,
        movement="Παραλαβή (+)",
        quantity=1,
        delta=1,
        transaction_id="tx-1",
    )
    row.update(overrides)
    return row


def test_empty_sheet_initializes_headers():
    ws = FakeWorksheet()
    headers, unknown = app.validate_and_migrate_headers(ws)
    assert headers == app.COLUMNS
    assert unknown == []
    assert ws.headers == app.COLUMNS


def test_missing_headers_are_appended_and_unknown_preserved():
    ws = FakeWorksheet(headers=["TransactionId", "LegacyColumn"])
    headers, unknown = app.validate_and_migrate_headers(ws)
    assert headers[0:2] == ["TransactionId", "LegacyColumn"]
    assert "MovementKind" in headers
    assert "PCCode" in headers
    assert "SerialNumber" in headers
    assert unknown == ["LegacyColumn"]


def test_duplicate_headers_rejected():
    ws = FakeWorksheet(headers=["TransactionId", "TransactionId"])
    with pytest.raises(app.SchemaError):
        app.validate_and_migrate_headers(ws)


def test_barcode_rejects_alphanumeric_but_other_preserves_it():
    with pytest.raises(app.InventoryError):
        app.resolve_identity("Barcode", "AB123")
    assert app.resolve_identity("Other", "AB123") == ("Other", "AB123", "")


def test_pc_sn_fallback_accepts_either_or_both():
    assert app.resolve_identity("QR", "", pc_code="111", serial_number="") == (
        "PC/SN",
        "PC:111",
        "",
    )
    assert app.resolve_identity("QR", "", pc_code="", serial_number="222") == (
        "PC/SN",
        "SN:222",
        "",
    )
    assert app.resolve_identity("QR", "", pc_code="111", serial_number="222") == (
        "PC/SN",
        "PC:111|SN:222",
        "",
    )


def test_no_primary_or_fallback_code_is_rejected():
    with pytest.raises(app.InventoryError):
        app.resolve_identity("QR", "", "", "")


def test_pc_sn_are_stored_and_searchable():
    row = base_row(
        CodeType="PC/SN",
        CodeValue="PC:111|SN:222",
        Barcode="",
        PCCode="111",
        SerialNumber="222",
    )
    df = app.records_to_dataframe([row])
    stock = app.stock_table(df)
    assert stock.iloc[0]["PCCode"] == "111"
    assert stock.iloc[0]["SerialNumber"] == "222"
    assert len(app.search_stock(stock, "111")[0]) == 1
    assert len(app.search_stock(stock, "222")[0]) == 1


def test_products_with_same_value_different_type_stay_separate():
    rows = [
        base_row(CodeType="Barcode", CodeValue="123", TransactionId="a"),
        base_row(CodeType="QR", CodeValue="123", Barcode="", TransactionId="b"),
    ]
    df = app.records_to_dataframe(rows)
    stock = app.stock_table(df)
    assert len(stock) == 2
    assert set(stock["CodeType"]) == {"Barcode", "QR"}


def test_reversal_rows_are_not_reversible_and_id_is_deterministic():
    original = base_row(TransactionId="original")
    reversal = base_row(
        TransactionId="reverse-original",
        VoidOf="original",
        MovementKind=app.REVERSAL,
        DeltaQty=-1,
    )
    df = app.records_to_dataframe([original, reversal])
    assert app.deterministic_reversal_id("original") == "reverse-original"
    assert app.reversible_rows(df).empty


def test_duplicate_reversal_prevention():
    original = base_row(TransactionId="original")
    reversal = base_row(
        TransactionId="reverse-original",
        VoidOf="original",
        MovementKind=app.REVERSAL,
        DeltaQty=-1,
    )
    df = app.records_to_dataframe([original, reversal])
    assert app.reversal_exists(df, "original")


def test_negative_stock_prevention():
    ws = FakeWorksheet(headers=app.COLUMNS, records=[base_row()])
    sale = base_row(
        TransactionId="sale",
        Κίνηση="Πώληση (-)",
        Ποσότητα=2,
        DeltaQty=-2,
    )
    with pytest.raises(app.InventoryError):
        app.append_stock_transaction(ws, sale)
    assert ws.appended == []


def test_compensation_is_appended_when_race_creates_negative_stock(monkeypatch):
    ws = FakeWorksheet(headers=app.COLUMNS, records=[base_row()])
    sale = base_row(
        TransactionId="sale",
        Κίνηση="Πώληση (-)",
        DeltaQty=-1,
    )

    calls = {"n": 0}
    real_load = app.load_data

    def raced_load(fake_ws):
        calls["n"] += 1
        if calls["n"] == 2:
            fake_ws.records.append(
                base_row(
                    TransactionId="other-sale",
                    Κίνηση="Πώληση (-)",
                    DeltaQty=-1,
                )
            )
        return real_load(fake_ws)

    monkeypatch.setattr(app, "load_data", raced_load)
    status = app.append_stock_transaction(ws, sale)
    assert status == "compensated"
    assert any(
        row["TransactionId"] == "compensation-sale"
        and row["MovementKind"] == app.COMPENSATION
        for row in ws.appended
    )


def test_gs1_datamatrix_parses_gtin_expiry_lot_serial():
    parsed = app.parse_gs1_datamatrix("01012345678901281726063010LOT12321SER456")
    assert parsed["gtin"] == "01234567890128"
    assert parsed["expiry_date"] == "2026-06-30"
    assert parsed["lot_number"] == "LOT123"
    assert parsed["serial_number"] == "SER456"


def test_expiry_month_year_stores_last_day():
    assert app.parse_expiry_date("02/2027") == "2027-02-28"


def test_merge_lookup_results_keeps_first_values_and_sources():
    merged = app.merge_lookup_results([
        {"product_name": "Cream", "brand": "", "category": "Cosmetics", "source": "Open Food Facts"},
        {"product_name": "Other", "brand": "Brand", "package_size": "50 ml", "source": "Open Beauty Facts"},
    ])
    assert merged["product_name"] == "Cream"
    assert merged["brand"] == "Brand"
    assert merged["package_size"] == "50 ml"
    assert merged["source"] == "Open Food Facts, Open Beauty Facts"


def test_search_includes_gtin_lot_and_raw_datamatrix():
    row = base_row(
        GTIN="01234567890128",
        LotNumber="LOT123",
        DataMatrixRawData="01012345678901281726063010LOT123",
    )
    stock = app.stock_table(app.records_to_dataframe([row]))
    assert len(app.search_stock(stock, "01234567890128")[0]) == 1
    assert len(app.search_stock(stock, "LOT123")[0]) == 1
    assert len(app.search_stock(stock, "260630")[0]) == 1
