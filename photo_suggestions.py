import hashlib
from typing import Any


def clean(value: Any) -> str:
    return str(value or "").strip()


def file_bytes(uploaded_file) -> bytes:
    if not uploaded_file:
        return b""
    return uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()


def file_hash(uploaded_file) -> str:
    data = file_bytes(uploaded_file)
    return hashlib.sha256(data).hexdigest() if data else ""


def suggest_fields(core, front_file=None, back_file=None, scan_result=None) -> dict[str, str]:
    scan_result = scan_result or {}
    output = {
        "product": "",
        "brand": "",
        "strength": "",
        "form": "",
        "expiry": "",
        "lot": "",
        "key": f"{file_hash(front_file)}:{file_hash(back_file)}:{clean(scan_result.get('raw', ''))}",
    }

    if front_file:
        old_calls = getattr(core, "MAX_FRONT_OCR_CALLS", 0)
        try:
            core.MAX_FRONT_OCR_CALLS = 2
            image = core.to_img(front_file)
            fields, _lines, _debug = core.detect_product_name(image)
            output["product"] = clean(fields.get("product_name") or fields.get("candidate", ""))
            output["brand"] = clean(fields.get("brand", ""))
            output["strength"] = clean(fields.get("strength", ""))
            output["form"] = clean(fields.get("dosage_form", ""))
        except Exception:
            pass
        finally:
            core.MAX_FRONT_OCR_CALLS = old_calls

    raw_value = clean(scan_result.get("raw", ""))
    if raw_value:
        try:
            parsed = core.parse_machine_readable_fields(raw_value)
            output["expiry"] = clean(parsed.get("expiry_date", ""))
            output["lot"] = clean(parsed.get("lot_number", ""))
        except Exception:
            pass

    if back_file and not output["expiry"]:
        try:
            image = core.to_img(back_file)
            fields, _lines, _debug = core.detect_back_expiry_ocr(image, file_hash(back_file))
            output["expiry"] = clean(fields.get("expiry_date", ""))
        except Exception:
            pass

    return output
