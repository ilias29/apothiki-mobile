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

    def delete_columns(self, start, end):
        del self.headers[start - 1:end]
        for row in self.records:
            for header in app.DEPRECATED_COLUMNS:
                row.pop(header, None)

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


def test_deprecated_headers_are_removed_and_not_unknown():
    ws = FakeWorksheet(headers=["TransactionId", "LookupSource", "PackageSize", "LegacyColumn", "LookupTimestamp"])
    headers, unknown = app.validate_and_migrate_headers(ws)
    assert "LookupSource" not in headers
    assert "LookupTimestamp" not in headers
    assert "PackageSize" not in headers
    assert unknown == ["LegacyColumn"]
    assert "LegacyColumn" in ws.headers


def test_deprecated_record_fields_are_ignored_and_product_fields_normalized():
    row = base_row(
        Προϊόν="Depon odis",
        Μάρκα="disperse pharma",
        DosageForm="Dispersible tablets",
        Strength="500 mg",
        LookupSource="old",
        LookupTimestamp="old",
        PackageSize="old",
    )
    df = app.records_to_dataframe([row])
    assert df.iloc[0]["Προϊόν"] == "DEPON ODIS"
    assert df.iloc[0]["Μάρκα"] == "DISPERSE PHARMA"
    assert df.iloc[0]["DosageForm"] == "DISPERSIBLE TABLETS"
    assert df.iloc[0]["Strength"] == "500 MG"
    assert not any(column in df.columns for column in app.DEPRECATED_COLUMNS)


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


def test_qr_text_parses_pc_sn_lot_expiry_separately():
    parsed = app.parse_machine_readable_fields("PC: PC123 SN: SN456 LOT: L789 EXP: 08 2028")
    assert parsed["pc_code"] == "PC123"
    assert parsed["serial_number"] == "SN456"
    assert parsed["lot_number"] == "L789"
    assert parsed["expiry_date"] == "2028-08-31"


def test_expiry_month_year_stores_last_day():
    assert app.parse_expiry_date("02/2027") == "2027-02-28"
    assert app.parse_expiry_date("02-2027") == "2027-02-28"
    assert app.parse_expiry_date("02.2027") == "2027-02-28"
    assert app.parse_expiry_date("31/12/2027") == "2027-12-31"
    assert app.parse_expiry_date("2027-12-31") == "2027-12-31"


def test_merge_lookup_results_keeps_first_values_and_providers_normalized():
    merged = app.merge_lookup_results([
        {"product_name": "Cream", "brand": "", "category": "Cosmetics", "provider": "skroutz.gr"},
        {"product_name": "Other", "brand": "Brand", "provider": "eof.gr"},
    ])
    assert merged["product_name"] == "CREAM"
    assert merged["brand"] == "BRAND"
    assert "package_size" not in merged
    assert merged["provider"] == "skroutz.gr, eof.gr"


def test_barcode_candidate_prefers_valid_ean13_and_preserves_digits():
    selection = app.select_barcode_candidate([
        {"type": "CODE128", "value": "LOT123"},
        {"type": "EAN-13", "value": "5206087700016"},
        {"type": "EAN-13", "value": "5206087700017"},
    ])
    assert selection["selected"]["type"] == "EAN-13"
    assert selection["selected"]["value"] == "5206087700016"
    assert selection["selected"]["checksum"] == "valid"


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


def test_expiry_status_rules_and_semester_labels():
    today = pd.Timestamp("2026-06-16").date()
    assert app.expiry_status("2026-06-15", today) == "expired"
    assert app.expiry_status("2026-07-16", today) == "expiring_soon"
    assert app.expiry_status("2026-09-14", today) == "expiring_soon"
    assert app.expiry_status("2026-09-15", today) == "valid"
    assert app.expiry_status("", today) == "without_expiry"
    assert app.expiry_semester("2026-06-30") == "A εξάμηνο 2026"
    assert app.expiry_semester("2026-07-01") == "B εξάμηνο 2026"


def test_stock_table_adds_expiry_alert_columns():
    row = base_row(ExpiryDate="2026-07-31")
    stock = app.stock_table(app.records_to_dataframe([row]))
    enriched = app.add_expiry_columns(stock, pd.Timestamp("2026-06-16").date())
    assert stock.iloc[0]["ExpiryDate"] == "2026-07-31"
    assert enriched.iloc[0]["ExpiryStatus"] == "expiring_soon"
    assert enriched.iloc[0]["Semester"] == "B εξάμηνο 2026"
    assert "Λήγει σε" in enriched.iloc[0]["ExpiryWarning"]


def test_expiry_reports_return_requested_buckets():
    today = pd.Timestamp("2026-06-16").date()
    rows = [
        base_row(TransactionId="expired", CodeValue="1", ExpiryDate="2026-06-15"),
        base_row(TransactionId="soon30", CodeValue="2", ExpiryDate="2026-07-01"),
        base_row(TransactionId="soon90", CodeValue="3", ExpiryDate="2026-09-14"),
        base_row(TransactionId="valid", CodeValue="4", ExpiryDate="2026-12-31"),
        base_row(TransactionId="missing", CodeValue="5", ExpiryDate=""),
    ]
    stock = app.stock_table(app.records_to_dataframe(rows))
    reports = app.expiry_reports(stock, today)
    assert len(reports["expired products"]) == 1
    assert len(reports["expiring in 30 days"]) == 1
    assert len(reports["expiring in 90 days"]) == 2
    assert len(reports["A εξάμηνο"]) == 1
    assert len(reports["B εξάμηνο"]) == 3
    assert len(reports["products without expiry date"]) == 1


def test_product_field_normalization_preserves_identifier_digits():
    normalized = app.normalize_product_fields(
        {
            "product_name": " depon  odis ",
            "brand": "  upsa - pharma ",
            "strength": "500 mg / 5 ml + 10 μg",
            "dosage_form": "orodispersible tabs",
            "barcode": " 5201234567890 ",
            "gtin": "05201234567890",
        }
    )
    assert normalized["product_name"] == "DEPON ODIS"
    assert normalized["brand"] == "UPSA - PHARMA"
    assert normalized["strength"] == "500 MG / 5 ML + 10 MCG"
    assert normalized["dosage_form"] == "ORODISPERSIBLE TABS"
    assert normalized["barcode"] == "5201234567890"
    assert normalized["gtin"] == "05201234567890"


def test_local_lookup_is_exact_barcode_or_gtin_only():
    rows = [
        base_row(Barcode="1112223334445", CodeValue="1112223334445", Προϊόν="Moducare", Μάρκα="Pharma", Strength="30 mg", DosageForm="caps"),
        base_row(TransactionId="gtin", Barcode="", CodeType="GTIN", CodeValue="01234567890128", GTIN="01234567890128", Προϊόν="Depon Odis"),
    ]
    stock = app.stock_table(app.records_to_dataframe(rows))
    by_barcode = app.lookup_local_database(stock, "1112223334445")
    by_gtin = app.lookup_local_database(stock, "01234567890128")
    assert by_barcode["product_name"] == "MODUCARE"
    assert by_barcode["strength"] == "30 MG"
    assert by_gtin["product_name"] == "DEPON ODIS"
    assert app.lookup_local_database(stock, "111222") is None


def test_gtin_validation_warns_without_changing_digits():
    assert app.validate_barcode_gtin("ABC") == ["Το barcode πρέπει να περιέχει μόνο ψηφία."]
    assert app.validate_barcode_gtin("5206087700017") == ["Το barcode έχει μη έγκυρο check digit."]
    assert app.validate_barcode_gtin(gtin="01234567890129") == ["Το GTIN έχει μη έγκυρο check digit."]
    assert app.validate_barcode_gtin(gtin="01234567890128") == []


def test_first_photo_uploader_exists_and_reference_image_is_preserved():
    source = open("app_inventory_search.py", encoding="utf-8").read()
    assert "Πρώτη/μπροστινή φωτογραφία αναφοράς (προαιρετική)" in source
    assert "front_photo_url=st.session_state.get(\"front_photo_data_url\", \"\")" in source
    row = app.make_transaction(
        code_type="Barcode",
        code_value="123",
        barcode="123",
        brand="Brand",
        product="Product",
        category="Φάρμακο",
        location_id=0,
        movement="Παραλαβή (+)",
        quantity=1,
        delta=1,
        front_photo_url="data:image/jpeg;base64,abc",
    )
    assert row["FrontPhotoUrl"] == "data:image/jpeg;base64,abc"


def test_detected_barcode_remains_after_another_form_field_changes():
    state = {"back_scan_image_hash": "hash-a", "back_scan_barcode": "5201234567890"}
    state["lookup_product_any"] = "Changed name"
    assert app.back_scan_values(state)[0] == "5201234567890"


def test_detected_barcode_remains_after_online_lookup():
    state = {"back_scan_image_hash": "hash-a", "back_scan_barcode": "5201234567890"}
    app.preserve_scanned_barcode_state(state, state["back_scan_barcode"])
    fields = app.product_text_fields_from_lookup(
        {
            "product_name": "Online Product",
            "brand": "Online Brand",
            "strength": "20 mg",
            "dosage_form": "Tablet",
            "barcode": "5209999999999",
        }
    )
    state.update(fields)
    app.preserve_scanned_barcode_state(state, state["back_scan_barcode"])

    assert state["back_scan_barcode"] == "5201234567890"
    assert state["lookup_last_search_value"] == "5201234567890"
    assert state["lookup_query"] == "5201234567890"
    assert state["lookup_scanned_barcode"] == "5201234567890"
    assert state["product_name"] == "ONLINE PRODUCT"
    assert state["brand"] == "ONLINE BRAND"
    assert state["strength"] == "20 MG"
    assert state["dosage_form"] == "TABLET"


def test_detected_barcode_remains_when_provider_returns_no_barcode():
    state = {"back_scan_image_hash": "hash-a", "back_scan_barcode": "5201234567890"}
    app.preserve_scanned_barcode_state(state, state["back_scan_barcode"])
    fields = app.product_text_fields_from_lookup({"product_name": "Online Product", "barcode": ""})
    state.update(fields)

    assert state["back_scan_barcode"] == "5201234567890"
    assert state["lookup_last_search_value"] == "5201234567890"
    assert state["lookup_query"] == "5201234567890"
    assert state["lookup_scanned_barcode"] == "5201234567890"
    assert "barcode" not in fields


def test_detected_barcode_remains_when_provider_returns_different_barcode():
    state = {"back_scan_image_hash": "hash-a", "back_scan_barcode": "5201234567890"}
    app.preserve_scanned_barcode_state(state, state["back_scan_barcode"])
    fields = app.product_text_fields_from_lookup({"product_name": "Other Product", "barcode": "5209999999999"})
    state.update(fields)

    assert state["back_scan_barcode"] == "5201234567890"
    assert state["lookup_last_search_value"] == "5201234567890"
    assert state["lookup_query"] == "5201234567890"
    assert state["lookup_scanned_barcode"] == "5201234567890"
    assert "barcode" not in fields


def test_detected_expiry_remains_after_another_form_field_changes():
    state = {"back_scan_image_hash": "hash-a", "back_scan_expiry": "2027-02-28"}
    state["lookup_brand_any"] = "Changed brand"
    assert app.back_scan_values(state)[2] == "2027-02-28"


def test_changing_product_name_does_not_clear_barcode():
    state = {"back_scan_image_hash": "hash-a", "back_scan_barcode": "5201234567890"}
    app.preserve_scanned_barcode_state(state, state["back_scan_barcode"])
    state["product_name"] = "Manual Product Name"

    assert state["back_scan_barcode"] == "5201234567890"
    assert state["lookup_last_search_value"] == "5201234567890"
    assert state["lookup_query"] == "5201234567890"
    assert state["lookup_scanned_barcode"] == "5201234567890"


def test_changing_expiry_does_not_clear_barcode():
    state = {
        "back_scan_image_hash": "hash-a",
        "back_scan_barcode": "5201234567890",
        "back_scan_expiry": "2027-02-28",
    }
    app.preserve_scanned_barcode_state(state, state["back_scan_barcode"])
    state["back_scan_expiry"] = "2028-03-31"

    assert state["back_scan_barcode"] == "5201234567890"
    assert state["lookup_last_search_value"] == "5201234567890"
    assert state["lookup_query"] == "5201234567890"
    assert state["lookup_scanned_barcode"] == "5201234567890"


def test_empty_rerun_result_cannot_overwrite_previously_detected_values():
    state = {
        "back_scan_image_hash": "hash-a",
        "back_scan_barcode": "5201234567890",
        "back_scan_gtin": "05201234567890",
        "back_scan_expiry": "2027-02-28",
    }
    app.apply_back_scan_result(state, "hash-a", {"barcode": "", "gtin": "", "expiry": ""})
    assert app.back_scan_values(state) == ("5201234567890", "05201234567890", "2027-02-28")


def test_new_back_image_hash_clears_only_previous_second_photo_scan():
    state = {
        "front_photo_data_url": "data:image/jpeg;base64,front",
        "front_photo_hash": "front-hash",
        "back_scan_image_hash": "hash-a",
        "back_scan_barcode": "5201234567890",
        "back_scan_expiry": "2027-02-28",
    }
    app.apply_back_scan_result(state, "hash-b", {"barcode": "", "gtin": "", "expiry": ""})
    assert state["back_scan_image_hash"] == "hash-b"
    assert "back_scan_barcode" not in state
    assert "back_scan_expiry" not in state
    assert state["front_photo_data_url"] == "data:image/jpeg;base64,front"
    assert state["front_photo_hash"] == "front-hash"


def test_new_search_clears_both_uploaded_photos_and_all_scan_state():
    state = {
        "front_photo_data_url": "data:image/jpeg;base64,front",
        "front_photo_hash": "front-hash",
        "back_scan_image_hash": "hash-a",
        "back_scan_barcode": "5201234567890",
        "back_scan_expiry": "2027-02-28",
    }
    app.clear_photo_scan_state(state)
    assert not any(key in state for key in ["front_photo_data_url", "front_photo_hash", *app.BACK_SCAN_STATE_KEYS])


def test_existing_product_confirmation_adds_exactly_plus_one_by_default():
    row = app.make_transaction(
        code_type="Barcode",
        code_value="123",
        barcode="123",
        brand="Brand",
        product="Product",
        category="Φάρμακο",
        location_id=0,
        movement="Παραλαβή (+)",
        quantity=app.DEFAULT_STOCK_ADD_QUANTITY,
        delta=app.DEFAULT_STOCK_ADD_QUANTITY,
    )
    assert app.DEFAULT_STOCK_ADD_QUANTITY == 1
    assert row["Ποσότητα"] == 1
    assert row["DeltaQty"] == 1
