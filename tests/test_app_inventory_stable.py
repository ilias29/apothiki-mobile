import numpy as np

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
    assert stable.APP_VERSION == "2026.09.10.1"
