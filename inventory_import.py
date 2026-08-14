import hashlib
import io
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd


DRAFT_COLUMNS = [
    "confirm",
    "ProductName",
    "EstimatedQty",
    "BarcodeOrGTIN",
    "ExpiryDate",
    "LotNumber",
    "Strength",
    "Category",
    "Confidence",
    "SourcePhoto",
    "Notes",
]

ALIASES = {
    "product": "ProductName",
    "productname": "ProductName",
    "name": "ProductName",
    "προιον": "ProductName",
    "ονομα": "ProductName",
    "estimatedqty": "EstimatedQty",
    "quantity": "EstimatedQty",
    "qty": "EstimatedQty",
    "totalquantity": "EstimatedQty",
    "ποσοτητα": "EstimatedQty",
    "barcode": "BarcodeOrGTIN",
    "ean": "BarcodeOrGTIN",
    "gtin": "BarcodeOrGTIN",
    "qr": "BarcodeOrGTIN",
    "barcodeorgtin": "BarcodeOrGTIN",
    "expiry": "ExpiryDate",
    "expirydate": "ExpiryDate",
    "ληξη": "ExpiryDate",
    "ημερομηνιαληξης": "ExpiryDate",
    "lot": "LotNumber",
    "lotnumber": "LotNumber",
    "παρτιδα": "LotNumber",
    "strength": "Strength",
    "περιεκτικοτητα": "Strength",
    "category": "Category",
    "κατηγορια": "Category",
    "confidence": "Confidence",
    "βεβαιοτητα": "Confidence",
    "sourcephoto": "SourcePhoto",
    "πηγη": "SourcePhoto",
    "notes": "Notes",
    "note": "Notes",
    "σημειωση": "Notes",
    "σημειωσεις": "Notes",
    "confirm": "confirm",
    "ok": "confirm",
}


def clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def normalized_header(value: Any) -> str:
    text = unicodedata.normalize("NFD", clean(value).lower())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[^0-9a-zα-ω]", "", text)


def truthy(value: Any) -> bool:
    return normalized_header(value) in {"1", "true", "yes", "y", "ναι", "nai", "ok", "x"}


def normalize_draft_columns(frame: pd.DataFrame, *, strict: bool = False, source: str = "ChatGPT") -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=DRAFT_COLUMNS)

    renamed = {column: ALIASES.get(normalized_header(column), clean(column)) for column in frame.columns}
    out = frame.rename(columns=renamed).copy()
    if out.columns.duplicated().any():
        duplicates = sorted(set(out.columns[out.columns.duplicated()].tolist()))
        raise ValueError("Διπλές στήλες μετά την αντιστοίχιση: " + ", ".join(duplicates))

    missing = [column for column in ("ProductName", "EstimatedQty") if column not in out.columns]
    if strict and missing:
        raise ValueError("Το αρχείο χρειάζεται στήλες προϊόντος και ποσότητας.")

    defaults = {
        "confirm": False,
        "ProductName": "",
        "EstimatedQty": 1,
        "BarcodeOrGTIN": "",
        "ExpiryDate": "",
        "LotNumber": "",
        "Strength": "",
        "Category": "Φάρμακο",
        "Confidence": "ChatGPT",
        "SourcePhoto": source,
        "Notes": "import_from_chatgpt",
    }
    for column, default in defaults.items():
        if column not in out.columns:
            out[column] = default

    out["ProductName"] = out["ProductName"].map(lambda value: clean(value).upper())
    out = out[out["ProductName"].ne("")].copy()
    if out.empty:
        raise ValueError("Το αρχείο δεν έχει καμία γραμμή προϊόντος.")

    quantities = pd.to_numeric(out["EstimatedQty"], errors="coerce")
    invalid = quantities.isna() | (quantities < 1) | (quantities % 1 != 0)
    if invalid.any():
        rows = ", ".join(str(index + 2) for index in out.index[invalid].tolist())
        raise ValueError(f"Μη έγκυρη ποσότητα στις γραμμές: {rows}. Επιτρέπονται μόνο ακέραιοι από 1 και πάνω.")
    out["EstimatedQty"] = quantities.astype(int)
    out["confirm"] = out["confirm"].map(truthy)

    for column in ["BarcodeOrGTIN", "ExpiryDate", "LotNumber", "Strength", "Category", "Confidence", "SourcePhoto", "Notes"]:
        out[column] = out[column].map(clean)
    out["Category"] = out["Category"].replace("", "Φάρμακο")
    out["Confidence"] = out["Confidence"].replace("", "ChatGPT")
    out["SourcePhoto"] = out["SourcePhoto"].replace("", source)
    out["Notes"] = out["Notes"].replace("", "import_from_chatgpt")
    return out[DRAFT_COLUMNS].reset_index(drop=True)


def read_inventory_bytes(data: bytes, filename: str) -> pd.DataFrame:
    if not data:
        raise ValueError("Το αρχείο είναι κενό.")
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".xlsx":
        frame = pd.read_excel(io.BytesIO(data), dtype=object)
    elif suffix == ".csv":
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "cp1253"):
            try:
                frame = pd.read_csv(io.BytesIO(data), sep=None, engine="python", encoding=encoding, dtype=object)
                break
            except UnicodeDecodeError as exc:
                last_error = exc
        else:
            raise ValueError("Το CSV δεν είναι σε υποστηριζόμενη κωδικοποίηση.") from last_error
    else:
        raise ValueError("Υποστηρίζονται μόνο αρχεία CSV και XLSX.")
    return normalize_draft_columns(frame, strict=True, source=filename or "ChatGPT file")


def import_batch_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def text_batch_id(text: str) -> str:
    return import_batch_id(clean(text).encode("utf-8"))


def transaction_id(batch_id: str, row_number: int) -> str:
    safe_batch = re.sub(r"[^0-9a-zA-Z_-]", "", clean(batch_id))[:24] or "manual"
    return f"chatgpt-import-{safe_batch}-{int(row_number):04d}"


def duplicate_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=DRAFT_COLUMNS)
    keys = ["ProductName", "Strength", "BarcodeOrGTIN", "ExpiryDate", "LotNumber"]
    mask = frame.duplicated(subset=keys, keep=False)
    return frame.loc[mask].copy()
