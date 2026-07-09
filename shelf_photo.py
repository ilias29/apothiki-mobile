import hashlib
import re
from typing import Any

import pandas as pd

STOP_TOKENS = {
    "δισκία", "δισκια", "καψάκια", "καψακια", "επικαλυμμένα", "επικαλυμμενα",
    "με", "λεπτό", "λεπτο", "υμένιο", "υμενιο", "χρήση", "χρηση", "δόση", "δοση",
    "tablets", "capsules", "solution", "drops", "cream", "syrup", "spray",
    "mg", "mcg", "ml", "iu", "caps", "tabs", "film", "coated",
}

NOISE_PATTERNS = [
    r"^\d+\s*(mg|mcg|ml|iu|g)$",
    r"^\d+\s*(δισκ|καψ|tabs|caps)",
    r"^(σε|με|για|και|των|την|του|της)\b",
    r"(ενδείξεις|αντενδείξεις|φυλάσσεται|χρήση|δοσολογία|παρενέργειες)",
]


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", clean(value)).strip()


def up(value: Any) -> str:
    return normalize_spaces(value).upper()


def file_bytes(uploaded_file) -> bytes:
    if not uploaded_file:
        return b""
    return uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()


def file_hash(uploaded_file) -> str:
    data = file_bytes(uploaded_file)
    return hashlib.sha256(data).hexdigest()[:12] if data else ""


def normalize_product_key(value: Any) -> str:
    text = up(value)
    text = re.sub(r"[^A-ZΑ-Ω0-9 ]", " ", text)
    text = re.sub(r"\b\d+(?:[,.]\d+)?\s*(?:MG|MCG|ML|IU|G|%)\b", " ", text)
    words = [word for word in text.split() if word.lower() not in STOP_TOKENS and len(word) >= 3]
    return " ".join(words[:3])


def looks_like_product_line(line: str) -> bool:
    text = normalize_spaces(line)
    if len(text) < 3 or len(text) > 70:
        return False
    if not re.search(r"[A-Za-zΑ-Ωα-ω]", text):
        return False
    lowered = text.lower()
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, lowered, re.I):
            return False
    letters = len(re.findall(r"[A-Za-zΑ-Ωα-ω]", text))
    digits = len(re.findall(r"\d", text))
    if letters < 3 or digits > letters + 10:
        return False
    key = normalize_product_key(text)
    return bool(key)


def extract_strength(line: str) -> str:
    match = re.search(r"\b\d+(?:[,.]\d+)?\s*(?:mg|mcg|μg|g|ml|iu|%)\b", line, re.I)
    return normalize_spaces(match.group(0)).upper() if match else ""


def extract_expiry(text: str) -> str:
    candidates = re.findall(r"(?:EXP|ΛΗΞΗ|ΛΗΓΕΙ)?\s*[:\-]?\s*(\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})", text, re.I)
    for candidate in candidates:
        value = clean(candidate)
        if re.search(r"\d", value):
            return value
    return ""


def extract_lot(text: str) -> str:
    match = re.search(r"\b(?:LOT|BATCH|ΠΑΡΤΙΔΑ)\s*[:\-]?\s*([A-ZΑ-Ω0-9\-]{3,})", text, re.I)
    return clean(match.group(1)).upper() if match else ""


def extract_codes(core, text: str) -> list[str]:
    results = []
    for raw in re.findall(r"\b\d{8,14}\b", text):
        if len(raw) in {8, 12, 13, 14}:
            try:
                if core.is_valid_gtin_check_digit(raw):
                    results.append(raw)
            except Exception:
                results.append(raw)
    return list(dict.fromkeys(results))


def ocr_lines(core, uploaded_file) -> tuple[list[str], dict[str, Any]]:
    debug: dict[str, Any] = {"file_hash": file_hash(uploaded_file), "ocr_available": "unknown", "errors": [], "raw_text": ""}
    if not uploaded_file:
        return [], debug
    try:
        status = core.tesseract_status()
        debug["ocr_available"] = status.get("available", "no")
        if status.get("available") != "yes":
            debug["errors"].append(status.get("reason", "tesseract unavailable"))
            return [], debug
        image = core.to_img(uploaded_file)
        if image is None:
            debug["errors"].append("image could not be read")
            return [], debug
        pil = core.ImageOps.exif_transpose(core.Image.fromarray(image)).convert("L")
        if pil.width < 1600:
            scale = min(3, max(2, int(1600 / max(1, pil.width))))
            pil = pil.resize((pil.width * scale, pil.height * scale), core.Image.Resampling.LANCZOS)
        pil = core.ImageEnhance.Contrast(pil).enhance(1.6)
        texts = []
        for psm in (6, 11):
            try:
                text = core.pytesseract.image_to_string(pil, lang="ell+eng", config=f"--psm {psm}", timeout=12)
                texts.append(text)
            except Exception as exc:
                debug["errors"].append(f"tesseract psm{psm}: {exc}")
        raw_text = "\n".join(texts)
        debug["raw_text"] = raw_text
        lines = [normalize_spaces(line) for line in raw_text.splitlines() if normalize_spaces(line)]
        return list(dict.fromkeys(lines)), debug
    except Exception as exc:
        debug["errors"].append(str(exc))
        return [], debug


def estimate_products_from_lines(core, lines: list[str], source_name: str = "") -> list[dict[str, Any]]:
    text = "\n".join(lines)
    codes = extract_codes(core, text)
    expiry = extract_expiry(text)
    lot = extract_lot(text)

    grouped: dict[str, dict[str, Any]] = {}
    for line in lines:
        if not looks_like_product_line(line):
            continue
        key = normalize_product_key(line)
        if not key:
            continue
        item = grouped.setdefault(key, {
            "confirm": False,
            "ProductName": up(line),
            "EstimatedQty": 0,
            "BarcodeOrGTIN": "",
            "ExpiryDate": expiry,
            "LotNumber": lot,
            "Strength": extract_strength(line),
            "Category": "Φάρμακο",
            "Confidence": "medium",
            "SourcePhoto": source_name,
            "Notes": "draft_from_shelf_photo",
        })
        item["EstimatedQty"] += 1
        if not item["Strength"]:
            item["Strength"] = extract_strength(line)
        if len(line) < len(item["ProductName"]):
            item["ProductName"] = up(line)

    rows = list(grouped.values())
    if len(rows) == 1 and len(codes) == 1:
        rows[0]["BarcodeOrGTIN"] = codes[0]
    for row in rows:
        row["EstimatedQty"] = max(1, int(row.get("EstimatedQty") or 1))
        if row["EstimatedQty"] >= 3:
            row["Confidence"] = "medium"
    return rows


def suggest_shelf_inventory(core, uploaded_files) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    debug_items: list[dict[str, Any]] = []
    for index, uploaded_file in enumerate(uploaded_files or [], start=1):
        name = getattr(uploaded_file, "name", f"photo_{index}")
        lines, debug = ocr_lines(core, uploaded_file)
        debug["source_photo"] = name
        debug["lines"] = lines
        debug_items.append(debug)
        all_rows.extend(estimate_products_from_lines(core, lines, name))

    if not all_rows:
        return pd.DataFrame(columns=["confirm", "ProductName", "EstimatedQty", "BarcodeOrGTIN", "ExpiryDate", "LotNumber", "Strength", "Category", "Confidence", "SourcePhoto", "Notes"]), debug_items

    df = pd.DataFrame(all_rows)
    cols = ["confirm", "ProductName", "EstimatedQty", "BarcodeOrGTIN", "ExpiryDate", "LotNumber", "Strength", "Category", "Confidence", "SourcePhoto", "Notes"]
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols], debug_items
