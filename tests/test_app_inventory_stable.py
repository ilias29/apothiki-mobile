import numpy as np
import pytest

import app_inventory_stable as stable


class FakeUpload:
    type = "image/jpeg"

    def getvalue(self):
        return b"real-camera-image-bytes"


def prepare_failed_line_decoder(monkeypatch):
    monkeypatch.setattr(stable.core, "to_img", lambda _upload: np.zeros((50, 100, 3), dtype=np.uint8))
    monkeypatch.setattr(stable.core, "detect_code", lambda _front, _back: ("Barcode", "", {"attempts": []}))
    monkeypatch.setattr(stable.st, "secrets", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "test-model"})
    monkeypatch.setattr(stable.st, "session_state", {})


def test_curved_barcode_accepts_complete_checksum_valid_ai_fallback(monkeypatch):
    """The exact barcode visible in the user's curved-bottle photo must survive."""
    prepare_failed_line_decoder(monkeypatch)
    calls = []

    def fake_read(image_bytes, **kwargs):
        calls.append((image_bytes, kwargs))
        return {"digits": "5200421900551", "confidence": "high"}

    monkeypatch.setattr(stable.ai_inventory, "read_barcode_digits", fake_read)

    assert stable.detect_barcode_from_camera(FakeUpload()) == "5200421900551"
    assert calls == [
        (
            b"real-camera-image-bytes",
            {"api_key": "test-key", "model": "test-model", "mime_type": "image/jpeg"},
        )
    ]
    assert stable.st.session_state["scan_debug"]["ai_digit_fallback"]["accepted"] is True


def test_curved_barcode_rejects_one_digit_error_even_with_high_confidence(monkeypatch):
    """High AI confidence never overrides an invalid EAN-13 check digit."""
    prepare_failed_line_decoder(monkeypatch)
    monkeypatch.setattr(
        stable.ai_inventory,
        "read_barcode_digits",
        lambda *_args, **_kwargs: {"digits": "5200421900552", "confidence": "high"},
    )

    assert stable.detect_barcode_from_camera(FakeUpload()) == ""
    fallback = stable.st.session_state["scan_debug"]["ai_digit_fallback"]
    assert fallback["accepted"] is False
    assert fallback["reason"] == "invalid_length_or_check_digit"


def test_stale_internal_codex_model_is_repaired_for_openai_api(monkeypatch):
    monkeypatch.setattr(stable.st, "secrets", {"OPENAI_MODEL": "gpt-5.6-terra"})
    assert stable.configured_openai_model() == "gpt-4.1-mini"


def test_live_scanner_accepts_valid_ean13_and_rejects_bad_check_digit():
    assert stable.validated_live_barcode("5200421900551") == "5200421900551"
    assert stable.validated_live_barcode("5200421900552") == ""


def test_pharmacy_starter_catalog_resolves_lamberts_without_online_lookup(monkeypatch):
    monkeypatch.setattr(stable, "read_products", lambda: stable.pd.DataFrame())
    product = stable.local_product_by_code("5055148407049")
    assert product["product_name"] == "LAMBERTS VITAMIN C 1000 MG 30 TABS"
    assert product["brand"] == "LAMBERTS"
    assert product["strength"] == "1000 MG"
    assert product["dosage_form"] == "TABS"
    assert product["package_size"] == "30 TABS"
    assert product["source"] == "Κατάλογος φαρμακείου"


def test_existing_sheet_product_still_has_priority_over_starter_catalog(monkeypatch):
    products = stable.pd.DataFrame([{
        "Barcode": "5055148407049", "ProductName": "CUSTOM NAME", "Brand": "CUSTOM",
        "Strength": "", "DosageForm": "", "Category": "Άλλο",
    }])
    monkeypatch.setattr(stable, "read_products", lambda: products)
    assert stable.local_product_by_code("5055148407049")["product_name"] == "CUSTOM NAME"


def test_new_scan_replaces_stale_manual_depon_barcode_and_product(monkeypatch):
    monkeypatch.setattr(stable.st, "session_state", {
        "active_barcode": "5200000000000",
        "manual_barcode": "5200000000000",
        "lookup_candidates": [{"product_name": "DEPON ODIS"}],
        "selected_candidate_index": 0,
    })
    assert stable.accept_detected_barcode("5055148407049") is True
    assert stable.st.session_state["active_barcode"] == "5055148407049"
    assert stable.st.session_state["manual_barcode"] == "5055148407049"
    assert "lookup_candidates" not in stable.st.session_state
    assert "selected_candidate_index" not in stable.st.session_state


def test_repeated_same_scan_does_not_clear_current_lookup(monkeypatch):
    candidates = [{"product_name": "LAMBERTS VITAMIN C 1000 MG 30 TABS"}]
    monkeypatch.setattr(stable.st, "session_state", {
        "active_barcode": "5055148407049",
        "manual_barcode": "5055148407049",
        "lookup_candidates": candidates,
    })
    assert stable.accept_detected_barcode("5055148407049") is False
    assert stable.st.session_state["lookup_candidates"] == candidates


def test_deployed_app_has_visible_diagnostic_version():
    assert stable.APP_VERSION == "2026.10.02.2"


def test_expiry_input_value_is_editable_and_never_fabricates_a_date():
    assert stable.expiry_input_value("2028-12-31") == "31/12/2028"
    assert stable.expiry_input_value("12/2028") == "31/12/2028"
    assert stable.expiry_input_value("") == ""
    assert stable.expiry_input_value("unknown") == "unknown"


def test_live_scanner_emits_one_token_until_barcode_leaves_frame():
    state = {}
    first = stable.next_live_scan_token(state, "5055148407049")
    assert first == "live:1:5055148407049"
    assert stable.next_live_scan_token(state, "5055148407049") == ""

    assert stable.next_live_scan_token(state, "") == ""
    second = stable.next_live_scan_token(state, "5055148407049")
    assert second == "live:2:5055148407049"


def test_recognized_barcode_adds_exactly_one_once_per_scan(monkeypatch):
    state = {}
    saved = []
    monkeypatch.setattr(
        stable,
        "local_product_by_code",
        lambda code: {
            "product_name": "LAMBERTS VITAMIN C 1000 MG 30 TABS",
            "brand": "LAMBERTS",
            "strength": "1000 MG",
            "dosage_form": "TABS",
            "category": "Συμπλήρωμα διατροφής",
        },
    )
    monkeypatch.setattr(stable, "save_inventory_item", lambda **kwargs: saved.append(kwargs))

    first = stable.auto_add_recognized_barcode(
        "5055148407049", "live:1:5055148407049", state=state
    )
    duplicate = stable.auto_add_recognized_barcode(
        "5055148407049", "live:1:5055148407049", state=state
    )

    assert first["status"] == "added"
    assert duplicate["status"] == "duplicate"
    assert len(saved) == 1
    assert saved[0]["code"] == "5055148407049"
    assert saved[0]["quantity"] == 1
    assert saved[0]["location_id"] == 0
    assert saved[0]["expiry_date"] == ""
    assert saved[0]["transaction_id"].startswith("auto-scan-")


def test_same_barcode_can_add_again_on_a_new_scan_event(monkeypatch):
    state = {}
    saved = []
    monkeypatch.setattr(
        stable,
        "local_product_by_code",
        lambda _code: {"product_name": "KNOWN PRODUCT", "category": "Άλλο"},
    )
    monkeypatch.setattr(stable, "save_inventory_item", lambda **kwargs: saved.append(kwargs))

    stable.auto_add_recognized_barcode("5055148407049", "live:1:5055148407049", state=state)
    stable.auto_add_recognized_barcode("5055148407049", "live:2:5055148407049", state=state)

    assert [item["quantity"] for item in saved] == [1, 1]


def test_unknown_barcode_is_not_auto_added(monkeypatch):
    state = {}
    monkeypatch.setattr(stable, "local_product_by_code", lambda _code: None)
    monkeypatch.setattr(
        stable,
        "save_inventory_item",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    result = stable.auto_add_recognized_barcode("5201234567890", "photo:abc", state=state)

    assert result["status"] == "unknown"
    assert "last_auto_stock_scan_token" not in state


def test_auto_scan_retry_reuses_transaction_id(monkeypatch):
    state = {}
    transaction_ids = []
    monkeypatch.setattr(
        stable,
        "local_product_by_code",
        lambda _code: {"product_name": "KNOWN PRODUCT", "category": "Άλλο"},
    )

    def flaky_save(**kwargs):
        transaction_ids.append(kwargs["transaction_id"])
        if len(transaction_ids) == 1:
            raise RuntimeError("temporary failure")

    monkeypatch.setattr(stable, "save_inventory_item", flaky_save)
    first = stable.auto_add_recognized_barcode("5055148407049", "photo:abc", state=state)
    retry = stable.auto_add_recognized_barcode("5055148407049", "photo:abc", state=state)

    assert first["status"] == "error"
    assert retry["status"] == "added"
    assert transaction_ids[0] == transaction_ids[1]


class MemoryStockWorksheet:
    def __init__(self):
        self.headers = stable.core.COLUMNS.copy()
        self.records = []
        self.before_append = None

    def row_values(self, _row):
        return self.headers.copy()

    def get_all_records(self):
        return [dict(row) for row in self.records]

    def append_rows(self, values, value_input_option=None):
        if self.before_append:
            callback, self.before_append = self.before_append, None
            callback()
        self.records.extend(dict(zip(self.headers, row)) for row in values)

    def append_row(self, values, value_input_option=None):
        if self.before_append:
            callback, self.before_append = self.before_append, None
            callback()
        self.records.append(dict(zip(self.headers, values)))

    def update(self, cell_range, values):
        if cell_range == "A1":
            self.headers = list(values[0])


def _saved_lot_fixture():
    ws = MemoryStockWorksheet()
    original = stable.core.make_transaction(
        code_type="Barcode",
        code_value="5055148407049",
        barcode="5055148407049",
        brand="LAMBERTS",
        product="MAXI-HAIR 60 TABS",
        category="Συμπλήρωμα",
        location_id=0,
        movement="Παραλαβή (+)",
        quantity=3,
        delta=3,
        strength="",
        dosage_form="TABS",
        expiry_date="2028-12-31",
        lot_number="LOT-OLD",
        transaction_id="original-receipt",
    )
    ws.records.append(original)
    data, _ = stable.core.load_data(ws)
    lot = stable.csa.stock_snapshot(data).iloc[0]
    return ws, original, lot


def test_saved_lot_can_be_edited_without_deleting_history(monkeypatch):
    ws, original, lot = _saved_lot_fixture()
    monkeypatch.setattr(stable.base_db, "update_product_details_from_transaction", lambda *_args: True)

    result = stable.edit_stock_lot(
        ws,
        lot,
        product_name="MAXI-HAIR 60 TABLETS",
        brand="LAMBERTS",
        strength="",
        dosage_form="TABLETS",
        category="Συμπλήρωμα",
        quantity=5,
        expiry_date="31/03/2029",
        lot_number="LOT-NEW",
        location_id=1,
        reason="Λάθος αρχική καταχώρηση",
        edit_id="edit-test-1",
    )

    corrected, _ = stable.core.load_data(ws)
    lots = stable.csa.stock_snapshot(corrected)
    stock = stable.core.stock_table(corrected)
    assert result == "saved"
    assert len(ws.records) == 3
    assert ws.records[0]["TransactionId"] == original["TransactionId"]
    assert stable.core.current_stock(corrected, "Barcode", "5055148407049", 0) == 0
    assert stable.core.current_stock(corrected, "Barcode", "5055148407049", 1) == 5
    assert lots.iloc[0]["Προϊόν"] == "MAXI-HAIR 60 TABLETS"
    assert lots.iloc[0]["LotNumber"] == "LOT-NEW"
    assert lots.iloc[0]["ExpiryDate"] == "2029-03-31"
    assert stock.iloc[0]["Προϊόν"] == "MAXI-HAIR 60 TABLETS"


def test_saved_lot_can_be_zeroed_without_deleting_history(monkeypatch):
    ws, original, lot = _saved_lot_fixture()
    monkeypatch.setattr(stable.base_db, "update_product_details_from_transaction", lambda *_args: True)

    result = stable.edit_stock_lot(
        ws,
        lot,
        product_name="MAXI-HAIR 60 TABS",
        brand="LAMBERTS",
        strength="",
        dosage_form="TABS",
        category="Συμπλήρωμα",
        quantity=0,
        expiry_date="31/12/2028",
        lot_number="LOT-OLD",
        location_id=0,
        reason="Μηδενισμός stock από χρήστη",
        edit_id="edit-zero-1",
    )

    corrected, _ = stable.core.load_data(ws)
    assert result == "saved"
    assert len(ws.records) == 2
    assert ws.records[0]["TransactionId"] == original["TransactionId"]
    assert stable.core.current_stock(corrected, "Barcode", "5055148407049", 0) == 0
    assert stable.csa.stock_snapshot(corrected).empty


def test_saved_lot_edit_retry_does_not_duplicate_stock_movements(monkeypatch):
    ws, _original, lot = _saved_lot_fixture()
    monkeypatch.setattr(stable.base_db, "update_product_details_from_transaction", lambda *_args: True)
    edit = dict(
        product_name="MAXI-HAIR 60 TABS",
        brand="LAMBERTS",
        strength="",
        dosage_form="TABS",
        category="Συμπλήρωμα",
        quantity=4,
        expiry_date="2028-12-31",
        lot_number="LOT-OLD",
        location_id=0,
        edit_id="edit-test-retry",
    )

    assert stable.edit_stock_lot(ws, lot, **edit) == "saved"
    assert stable.edit_stock_lot(ws, lot, **edit) == "duplicate"
    corrected, _ = stable.core.load_data(ws)
    assert len(ws.records) == 3
    assert stable.core.current_stock(corrected, "Barcode", "5055148407049", 0) == 4


def test_saved_lot_edit_refuses_stale_quantity(monkeypatch):
    ws, _original, lot = _saved_lot_fixture()
    consumed = stable.core.make_transaction(
        code_type="Barcode",
        code_value="5055148407049",
        barcode="5055148407049",
        brand="LAMBERTS",
        product="MAXI-HAIR 60 TABS",
        category="Συμπλήρωμα",
        location_id=0,
        movement="Πώληση (-)",
        quantity=2,
        delta=-2,
        lot_number="LOT-OLD",
        expiry_date="2028-12-31",
        transaction_id="later-sale",
    )
    ws.records.append(consumed)
    monkeypatch.setattr(stable.base_db, "update_product_details_from_transaction", lambda *_args: True)

    with pytest.raises(stable.core.InventoryError, match="υπόλοιπο της παρτίδας άλλαξε"):
        stable.edit_stock_lot(
            ws,
            lot,
            product_name="MAXI-HAIR 60 TABS",
            brand="LAMBERTS",
            strength="",
            dosage_form="TABS",
            category="Συμπλήρωμα",
            quantity=4,
            expiry_date="2028-12-31",
            lot_number="LOT-OLD",
            location_id=0,
            edit_id="edit-stale",
        )
    assert len(ws.records) == 2


def test_saved_lot_edit_compensates_if_concurrent_sale_would_go_negative(monkeypatch):
    ws, _original, lot = _saved_lot_fixture()
    concurrent_sale = stable.core.make_transaction(
        code_type="Barcode",
        code_value="5055148407049",
        barcode="5055148407049",
        brand="LAMBERTS",
        product="MAXI-HAIR 60 TABS",
        category="Συμπλήρωμα",
        location_id=0,
        movement="Πώληση (-)",
        quantity=3,
        delta=-3,
        lot_number="LOT-OLD",
        expiry_date="2028-12-31",
        transaction_id="concurrent-sale",
    )
    ws.before_append = lambda: ws.records.append(concurrent_sale)
    monkeypatch.setattr(stable.base_db, "update_product_details_from_transaction", lambda *_args: True)

    result = stable.edit_stock_lot(
        ws,
        lot,
        product_name="MAXI-HAIR 60 TABS",
        brand="LAMBERTS",
        strength="",
        dosage_form="TABS",
        category="Συμπλήρωμα",
        quantity=0,
        expiry_date="2028-12-31",
        lot_number="LOT-OLD",
        location_id=0,
        edit_id="edit-race",
    )

    corrected, _ = stable.core.load_data(ws)
    compensation_rows = corrected[corrected["MovementKind"].eq(stable.core.COMPENSATION)]
    assert result == "race_compensated"
    assert stable.core.current_stock(corrected, "Barcode", "5055148407049", 0) == 0
    assert len(compensation_rows) == 1
    assert compensation_rows.iloc[0]["VoidOf"] == "stock-edit-" + stable.hashlib.sha256(b"edit-race").hexdigest()[:24] + "-out"


def test_excel_catalog_is_used_before_online_lookup(monkeypatch):
    monkeypatch.setattr(stable, "read_products", lambda: stable.pd.DataFrame())
    monkeypatch.setattr(
        stable,
        "lookup_pharmacy_product",
        lambda code: {"product_name": "AVENE TEST 40ML", "barcodes": "111|222"}
        if code == "222"
        else None,
    )
    product = stable.local_product_by_code("222")
    assert product["product_name"] == "AVENE TEST 40ML"
    assert product["brand"] == "AVENE"
    assert product["source"] == "Βάση προϊόντων φαρμακείου"


def test_scanned_cod_liver_oil_is_in_pharmacy_catalog(monkeypatch):
    monkeypatch.setattr(stable, "read_products", lambda: stable.pd.DataFrame())
    product = stable.local_product_by_code("5055148400620")
    assert product["product_name"] == "LAMBERTS COD LIVER OIL 1000 MG 180 CAPS"
    assert product["strength"] == "1000 MG"
    assert product["dosage_form"] == "CAPS"
    assert product["package_size"] == "180 CAPS"


def test_catalog_tab_data_includes_zero_stock_starter_products(monkeypatch):
    monkeypatch.setattr(stable, "read_products", lambda: stable.pd.DataFrame())
    catalog = stable.catalog_dataframe()
    assert len(catalog) >= len(stable.LAMBERTS_PRODUCTS)
    row = catalog[catalog["Barcode"] == "5055148400620"].iloc[0]
    assert row["Προϊόν"] == "LAMBERTS COD LIVER OIL 1000 MG 180 CAPS"
    assert row["Πηγή"] == "Κατάλογος φαρμακείου"


def test_scanned_multi_guard_adr_60_resolves_locally(monkeypatch):
    monkeypatch.setattr(stable, "read_products", lambda: stable.pd.DataFrame())
    product = stable.local_product_by_code("5055148412708")
    assert product["product_name"] == "LAMBERTS MULTI-GUARD ADR 60 TABS"
    assert product["brand"] == "LAMBERTS"
    assert product["dosage_form"] == "TABS"
    assert product["package_size"] == "60 TABS"


def test_all_products_from_new_lamberts_invoice_are_in_catalog(monkeypatch):
    monkeypatch.setattr(stable, "read_products", lambda: stable.pd.DataFrame())
    expected = {
        "5055148412708": "LAMBERTS MULTI-GUARD ADR 60 TABS",
        "5055148412616": "LAMBERTS TURMERIC FAST RELEASE 60 TABS",
        "5055148410544": "LAMBERTS VITAMIN D3 4000 IU 120 CAPS",
        "5055148401351": "LAMBERTS MAXI HAIR NEW FORMULA 60 TABS",
        "5055148414849": "LAMBERTS B-50 COMPLEX 120 TABS",
        "5055148400217": "LAMBERTS B-50 COMPLEX 60 TABS",
        "5055148408909": "LAMBERTS CO-Q10 30 MG 30 CAPS",
        "5055148411008": "LAMBERTS L-METHIONINE 500 MG 60 CAPS",
        "5055148410674": "LAMBERTS OMEGA 3 ULTRA 1300 MG 60 CAPS",
    }
    for barcode, name in expected.items():
        product = stable.local_product_by_code(barcode)
        assert product is not None
        assert product["product_name"] == name


def test_new_maxi_hair_packaging_barcode_resolves_locally(monkeypatch):
    monkeypatch.setattr(stable, "read_products", lambda: stable.pd.DataFrame())
    product = stable.local_product_by_code("5055148411252")
    assert product["product_name"] == "LAMBERTS MAXI-HAIR 60 TABS"
    assert product["brand"] == "LAMBERTS"
    assert product["dosage_form"] == "TABS"
    assert product["package_size"] == "60 TABS"
