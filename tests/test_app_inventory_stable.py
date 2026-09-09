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
