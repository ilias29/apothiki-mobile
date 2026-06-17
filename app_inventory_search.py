import hashlib
import re
import calendar
import shutil
import time
import uuid
from datetime import date, datetime
from typing import Any

import requests

import cv2
import gspread
import numpy as np
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:
    import pytesseract
except Exception as exc:
    pytesseract = None
    PYTESSERACT_IMPORT_ERROR = exc
else:
    PYTESSERACT_IMPORT_ERROR = None

try:
    from pyzbar.pyzbar import ZBarSymbol, decode as pyzbar_decode
except Exception as exc:
    pyzbar_decode = None
    ZBarSymbol = None
    PYZBAR_IMPORT_ERROR = exc
else:
    PYZBAR_IMPORT_ERROR = None

SCOPE = ["https://www.googleapis.com/auth/spreadsheets", 
        "https://www.googleapis.com/auth/drive" ,
        ]
SHEET_NAME = "Apothiki_Cloud"
WS_NAME = "Transactions"

LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}
CATEGORIES = ["Φάρμακο", "Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Άλλο"]
FRONT_OCR_TIMEOUT_SECONDS = 12
BACK_OCR_TIMEOUT_SECONDS = 8
MAX_FRONT_OCR_CALLS = 2
MAX_BACK_EXPIRY_OCR_CALLS = 4
MIN_VALID_EXPIRY_YEAR = 2020



DEPRECATED_COLUMNS = {"LookupSource", "LookupTimestamp", "PackageSize"}

COLUMNS = [
    "TransactionId",
    "Timestamp",
    "Ημερομηνία",
    "CodeType",
    "CodeValue",
    "Barcode",
    "PCCode",
    "GTIN",
    "SerialNumber",
    "LotNumber",
    "ExpiryDate",
    "QRRawData",
    "DataMatrixRawData",
    "Strength",
    "DosageForm",
    "Μάρκα",
    "Προϊόν",
    "Κατηγορία",
    "LocationId",
    "Τοποθεσία",
    "Κίνηση",
    "Ποσότητα",
    "DeltaQty",
    "FrontPhotoUrl",
    "BackPhotoUrl",
    "Σημείωση",
    "Voided",
    "VoidOf",
    "MovementKind",
]

TRUE_VALUES = {"true", "1", "yes", "y", "ναι", "nai"}
NORMAL = "Normal"
REVERSAL = "Reversal"
COMPENSATION = "Compensation"


class InventoryError(Exception):
    pass


class SchemaError(InventoryError):
    pass


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_bool(value: Any) -> bool:
    return clean(value).lower() in TRUE_VALUES


def resolve_identity(
    code_type: str,
    raw_value: Any,
    pc_code: Any = "",
    serial_number: Any = "",
) -> tuple[str, str, str]:
    value = clean(raw_value)
    pc = clean(pc_code)
    serial = clean(serial_number)

    if value:
        if code_type == "Barcode" and not value.isdigit():
            raise InventoryError(
                "Το Barcode δέχεται μόνο αριθμούς. Για αλφαριθμητικό κωδικό "
                "διάλεξε QR ή Other."
            )
        return code_type, value, value if code_type == "Barcode" else ""

    if pc or serial:
        parts = []
        if pc:
            parts.append(f"PC:{pc}")
        if serial:
            parts.append(f"SN:{serial}")
        return "PC/SN", "|".join(parts), ""

    raise InventoryError(
        "Βάλε Barcode/QR/Other ή τουλάχιστον έναν από τους κωδικούς PC και SN."
    )


def validate_code(code_type: str, raw_value: Any) -> tuple[str, str]:
    resolved_type, value, barcode = resolve_identity(code_type, raw_value)
    if resolved_type == "PC/SN":
        raise InventoryError("Χρειάζεται PC ή SN για fallback καταχώρηση.")
    return value, barcode


def normalize_spaces(value: Any) -> str:
    return re.sub(r"\s+", " ", clean(value)).strip()


def normalize_strength(value: Any) -> str:
    text = normalize_spaces(value)
    text = re.sub(r"\bmcg\b|μg", "MCG", text, flags=re.I)
    text = re.sub(r"\bmg\b", "MG", text, flags=re.I)
    text = re.sub(r"\bml\b", "ML", text, flags=re.I)
    text = re.sub(r"\biu\b", "IU", text, flags=re.I)
    text = re.sub(r"\bg\b", "G", text, flags=re.I)
    return text


def normalize_product_fields(fields: dict[str, Any]) -> dict[str, str]:
    return {
        "product_name": normalize_spaces(fields.get("product_name", "")).upper(),
        "brand": normalize_spaces(fields.get("brand", fields.get("brand_or_company", ""))).upper(),
        "strength": normalize_strength(fields.get("strength", "")),
        "dosage_form": normalize_spaces(fields.get("dosage_form", "")).upper(),
        "barcode": clean(fields.get("barcode", "")),
        "gtin": clean(fields.get("gtin", "")),
        "category": clean(fields.get("category", "")),
    }


def is_valid_gtin_check_digit(gtin: str) -> bool:
    digits = clean(gtin)
    if not digits.isdigit() or len(digits) not in {8, 12, 13, 14}:
        return False
    body = [int(ch) for ch in digits[:-1]]
    total = 0
    for idx, digit in enumerate(reversed(body), start=1):
        total += digit * (3 if idx % 2 else 1)
    return (10 - (total % 10)) % 10 == int(digits[-1])


def validate_barcode_gtin(barcode: str = "", gtin: str = "") -> list[str]:
    warnings = []
    if clean(barcode) and not clean(barcode).isdigit():
        warnings.append("Το barcode πρέπει να περιέχει μόνο ψηφία.")
    elif len(clean(barcode)) in {8, 13} and not is_valid_gtin_check_digit(clean(barcode)):
        warnings.append("Το barcode έχει μη έγκυρο check digit.")
    if clean(gtin):
        if not clean(gtin).isdigit() or len(clean(gtin)) not in {8, 12, 13, 14}:
            warnings.append("Το GTIN πρέπει να έχει 8, 12, 13 ή 14 ψηφία.")
        elif not is_valid_gtin_check_digit(clean(gtin)):
            warnings.append("Το GTIN έχει μη έγκυρο check digit.")
    return warnings


def deterministic_reversal_id(original_id: str) -> str:
    return f"reverse-{clean(original_id)}"


def deterministic_compensation_id(transaction_id: str) -> str:
    return f"compensation-{clean(transaction_id)}"


def _delete_sheet_columns(ws, column_numbers: list[int]) -> None:
    for column_number in sorted(column_numbers, reverse=True):
        if hasattr(ws, "delete_columns"):
            ws.delete_columns(column_number, column_number)
        elif hasattr(ws, "delete_cols"):
            ws.delete_cols(column_number, column_number)
        else:
            raise AttributeError("Worksheet does not support column deletion")


def validate_and_migrate_headers(ws) -> tuple[list[str], list[str]]:
    headers = [clean(h) for h in ws.row_values(1)]
    if not headers or not any(headers):
        ws.update("A1", [COLUMNS])
        return COLUMNS.copy(), []
    if any(not h for h in headers):
        raise SchemaError(
            "Η πρώτη γραμμή του Google Sheet έχει κενές επικεφαλίδες. "
            "Διόρθωσέ τες πριν συνεχίσεις."
        )
    duplicates = sorted({h for h in headers if headers.count(h) > 1})
    if duplicates:
        raise SchemaError(
            "Υπάρχουν διπλές επικεφαλίδες στο Google Sheet: "
            + ", ".join(duplicates)
        )
    deprecated_indexes = [
        index
        for index, header in enumerate(headers, start=1)
        if header in DEPRECATED_COLUMNS
    ]
    if deprecated_indexes:
        try:
            _delete_sheet_columns(ws, deprecated_indexes)
            headers = [clean(h) for h in ws.row_values(1)]
        except Exception:
            headers = [header for header in headers if header not in DEPRECATED_COLUMNS]
            ws.update("A1", [headers])

    missing = [column for column in COLUMNS if column not in headers]
    if missing:
        headers = headers + missing
        ws.update("A1", [headers])
    unknown = [header for header in headers if header not in COLUMNS and header not in DEPRECATED_COLUMNS]
    return headers, unknown


def records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = ""

    text_columns = [
        "TransactionId",
        "Timestamp",
        "Ημερομηνία",
        "CodeType",
        "CodeValue",
        "Barcode",
        "PCCode",
        "GTIN",
        "SerialNumber",
        "LotNumber",
        "ExpiryDate",
        "QRRawData",
        "DataMatrixRawData",
        "Strength",
        "DosageForm",
        "VoidOf",
        "MovementKind",
    ]
    for column in text_columns:
        df[column] = df[column].fillna("").astype(str)

    legacy_code = df["CodeValue"].str.strip().eq("") & df["Barcode"].str.strip().ne("")
    df.loc[legacy_code, "CodeType"] = "Barcode"
    df.loc[legacy_code, "CodeValue"] = df.loc[legacy_code, "Barcode"]

    legacy_timestamp = df["Timestamp"].str.strip().eq("")
    df.loc[legacy_timestamp, "Timestamp"] = df.loc[legacy_timestamp, "Ημερομηνία"]

    legacy_kind = df["MovementKind"].str.strip().eq("")
    df.loc[legacy_kind, "MovementKind"] = NORMAL

    df["Προϊόν"] = df["Προϊόν"].map(lambda value: normalize_spaces(value).upper())
    df["Μάρκα"] = df["Μάρκα"].map(lambda value: normalize_spaces(value).upper())
    df["DosageForm"] = df["DosageForm"].map(lambda value: normalize_spaces(value).upper())
    df["Strength"] = df["Strength"].map(normalize_strength)

    df["DeltaQty"] = pd.to_numeric(df["DeltaQty"], errors="coerce").fillna(0).astype(int)
    df["LocationId"] = pd.to_numeric(df["LocationId"], errors="coerce").fillna(-1).astype(int)
    df["Voided"] = df["Voided"].map(normalize_bool)
    return df[COLUMNS]


def load_data(ws) -> tuple[pd.DataFrame, list[str]]:
    _, unknown = validate_and_migrate_headers(ws)
    try:
        records = ws.get_all_records()
    except Exception as exc:
        raise InventoryError("Δεν ήταν δυνατή η ανάγνωση των κινήσεων.") from exc
    return records_to_dataframe(records), unknown


def append_row(ws, row: dict[str, Any]) -> None:
    headers, _ = validate_and_migrate_headers(ws)
    writable_headers = [header for header in headers if header not in DEPRECATED_COLUMNS]
    try:
        ws.append_row(
            [row.get(header, "") for header in writable_headers],
            value_input_option="RAW",
        )
    except Exception as exc:
        raise InventoryError("Δεν ήταν δυνατή η αποθήκευση της κίνησης.") from exc


def active_movements(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return df[~df["Voided"].map(bool)].copy()


def current_stock(df: pd.DataFrame, code_type: str, code_value: str, location_id: int) -> int:
    active = active_movements(df)
    if active.empty:
        return 0
    mask = (
        active["CodeType"].astype(str).eq(str(code_type))
        & active["CodeValue"].astype(str).eq(str(code_value))
        & active["LocationId"].eq(int(location_id))
    )
    return int(active.loc[mask, "DeltaQty"].sum())


def transaction_exists(df: pd.DataFrame, transaction_id: str) -> bool:
    return df["TransactionId"].astype(str).eq(clean(transaction_id)).any()


def reversal_exists(df: pd.DataFrame, original_id: str) -> bool:
    original_id = clean(original_id)
    return (
        df["VoidOf"].astype(str).eq(original_id).any()
        or transaction_exists(df, deterministic_reversal_id(original_id))
    )


def reversible_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    reversed_ids = set(
        df.loc[df["VoidOf"].astype(str).str.strip().ne(""), "VoidOf"].astype(str)
    )
    mask = (
        df["TransactionId"].astype(str).str.strip().ne("")
        & ~df["TransactionId"].astype(str).isin(reversed_ids)
        & df["VoidOf"].astype(str).str.strip().eq("")
        & ~df["MovementKind"].astype(str).isin([REVERSAL, COMPENSATION])
        & ~df["Voided"].map(bool)
    )
    return df[mask].copy()


def make_transaction(
    *,
    code_type: str,
    code_value: str,
    barcode: str,
    brand: str,
    product: str,
    category: str,
    location_id: int,
    movement: str,
    quantity: int,
    delta: int,
    pc_code: str = "",
    gtin: str = "",
    serial_number: str = "",
    lot_number: str = "",
    expiry_date: str = "",
    qr_raw_data: str = "",
    datamatrix_raw_data: str = "",
    strength: str = "",
    dosage_form: str = "",
    note: str = "",
    transaction_id: str | None = None,
    void_of: str = "",
    movement_kind: str = NORMAL,
) -> dict[str, Any]:
    now = datetime.now()
    return {
        "TransactionId": transaction_id or str(uuid.uuid4()),
        "Timestamp": now.isoformat(timespec="seconds"),
        "Ημερομηνία": now.strftime("%Y-%m-%d %H:%M"),
        "CodeType": clean(code_type),
        "CodeValue": clean(code_value),
        "Barcode": clean(barcode),
        "PCCode": clean(pc_code),
        "GTIN": clean(gtin),
        "SerialNumber": clean(serial_number),
        "LotNumber": clean(lot_number),
        "ExpiryDate": clean(expiry_date),
        "QRRawData": clean(qr_raw_data),
        "DataMatrixRawData": clean(datamatrix_raw_data),
        "Strength": normalize_strength(strength),
        "DosageForm": normalize_spaces(dosage_form).upper(),
        "Μάρκα": normalize_spaces(brand).upper(),
        "Προϊόν": normalize_spaces(product).upper(),
        "Κατηγορία": clean(category),
        "LocationId": int(location_id),
        "Τοποθεσία": LOCATIONS[int(location_id)],
        "Κίνηση": clean(movement),
        "Ποσότητα": int(quantity),
        "DeltaQty": int(delta),
        "FrontPhotoUrl": "",
        "BackPhotoUrl": "",
        "Σημείωση": clean(note),
        "Voided": False,
        "VoidOf": clean(void_of),
        "MovementKind": clean(movement_kind) or NORMAL,
    }


def append_stock_transaction(ws, row: dict[str, Any]) -> str:
    fresh, _ = load_data(ws)
    txid = clean(row["TransactionId"])
    if transaction_exists(fresh, txid):
        return "duplicate"

    delta = int(row["DeltaQty"])
    if delta < 0:
        available = current_stock(
            fresh, row["CodeType"], row["CodeValue"], int(row["LocationId"])
        )
        if available + delta < 0:
            raise InventoryError(
                f"Η κίνηση θα έκανε το stock αρνητικό. "
                f"Διαθέσιμα: {available}, ζητήθηκαν: {abs(delta)}."
            )

    append_row(ws, row)
    if delta >= 0:
        return "saved"

    verified, _ = load_data(ws)
    after = current_stock(
        verified, row["CodeType"], row["CodeValue"], int(row["LocationId"])
    )
    if after >= 0:
        return "saved"

    compensation_id = deterministic_compensation_id(txid)
    if not transaction_exists(verified, compensation_id):
        compensation = make_transaction(
            code_type=row["CodeType"],
            code_value=row["CodeValue"],
            barcode=row["Barcode"],
            pc_code=row.get("PCCode", ""),
            gtin=row.get("GTIN", ""),
            serial_number=row.get("SerialNumber", ""),
            lot_number=row.get("LotNumber", ""),
            expiry_date=row.get("ExpiryDate", ""),
            qr_raw_data=row.get("QRRawData", ""),
            datamatrix_raw_data=row.get("DataMatrixRawData", ""),
            strength=row.get("Strength", ""),
            dosage_form=row.get("DosageForm", ""),
            brand=row["Μάρκα"],
            product=row["Προϊόν"],
            category=row["Κατηγορία"],
            location_id=int(row["LocationId"]),
            movement="Αυτόματη αντιστάθμιση (+)",
            quantity=abs(delta),
            delta=abs(delta),
            note=f"Αυτόματη αντιστάθμιση για {txid}",
            transaction_id=compensation_id,
            void_of=txid,
            movement_kind=COMPENSATION,
        )
        append_row(ws, compensation)
    return "compensated"


def append_reversal(ws, original: pd.Series, reason: str = "") -> str:
    original_id = clean(original["TransactionId"])
    if not original_id:
        raise InventoryError("Η παλιά κίνηση δεν έχει TransactionId και δεν αναστρέφεται.")

    fresh, _ = load_data(ws)
    if reversal_exists(fresh, original_id):
        return "duplicate"

    kind = clean(original.get("MovementKind", NORMAL))
    if clean(original.get("VoidOf", "")) or kind in {REVERSAL, COMPENSATION}:
        raise InventoryError("Μια αναστροφή ή αντιστάθμιση δεν μπορεί να αναστραφεί.")

    reverse_delta = -int(original["DeltaQty"])
    row = make_transaction(
        code_type=original["CodeType"],
        code_value=original["CodeValue"],
        barcode=original["Barcode"],
        pc_code=original.get("PCCode", ""),
        gtin=original.get("GTIN", ""),
        serial_number=original.get("SerialNumber", ""),
        lot_number=original.get("LotNumber", ""),
        expiry_date=original.get("ExpiryDate", ""),
        qr_raw_data=original.get("QRRawData", ""),
        datamatrix_raw_data=original.get("DataMatrixRawData", ""),
        strength=original.get("Strength", ""),
        dosage_form=original.get("DosageForm", ""),
        brand=original["Μάρκα"],
        product=original["Προϊόν"],
        category=original["Κατηγορία"],
        location_id=int(original["LocationId"]),
        movement="Αναστροφή (+)" if reverse_delta > 0 else "Αναστροφή (-)",
        quantity=abs(reverse_delta),
        delta=reverse_delta,
        note=f"Αναστροφή {original_id}. {clean(reason)}",
        transaction_id=deterministic_reversal_id(original_id),
        void_of=original_id,
        movement_kind=REVERSAL,
    )
    return append_stock_transaction(ws, row)


def stock_table(df: pd.DataFrame) -> pd.DataFrame:
    output_columns = [
        "CodeType", "CodeValue", "Barcode", "PCCode", "GTIN", "SerialNumber",
        "LotNumber", "ExpiryDate", "ExpiryStatus", "ExpiryWarning", "Semester",
        "QRRawData", "DataMatrixRawData", "Μάρκα", "Προϊόν", "Κατηγορία",
        "Strength", "DosageForm", "Αποθήκη", "Κύριο Κτήριο",
        "Πρώτος Όροφος", "Σύνολο",
    ]
    data = active_movements(df)
    if data.empty:
        return pd.DataFrame(columns=output_columns)

    identity = ["CodeType", "CodeValue"]
    data = data.copy()
    data["Timestamp_dt"] = pd.to_datetime(data["Timestamp"], errors="coerce")
    latest = (
        data.sort_values("Timestamp_dt")
        .groupby(identity, dropna=False)
        .tail(1)[identity + ["Barcode", "PCCode", "GTIN", "SerialNumber", "LotNumber", "ExpiryDate", "QRRawData", "DataMatrixRawData", "Μάρκα", "Προϊόν", "Κατηγορία", "Strength", "DosageForm"]]
    )
    grouped = data.groupby(identity + ["LocationId"], dropna=False)["DeltaQty"].sum().reset_index()
    pivot = grouped.pivot_table(
        index=identity, columns="LocationId", values="DeltaQty", fill_value=0
    ).reset_index()
    pivot = pivot.rename(columns={0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"})
    for column in ["Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος"]:
        if column not in pivot.columns:
            pivot[column] = 0
    pivot["Σύνολο"] = pivot["Αποθήκη"] + pivot["Κύριο Κτήριο"] + pivot["Πρώτος Όροφος"]
    stock = pivot.merge(latest, on=identity, how="left").sort_values("Σύνολο", ascending=False)
    stock = add_expiry_columns(stock)
    return stock[output_columns]


def expiry_semester(expiry: Any) -> str:
    expiry_dt = pd.to_datetime(clean(expiry), errors="coerce")
    if pd.isna(expiry_dt):
        return ""
    label = "A" if expiry_dt.month <= 6 else "B"
    return f"{label} εξάμηνο {expiry_dt.year}"


def expiry_status(expiry: Any, today: date | None = None) -> str:
    today = today or date.today()
    expiry_dt = pd.to_datetime(clean(expiry), errors="coerce")
    if pd.isna(expiry_dt):
        return "without_expiry"
    expiry_date = expiry_dt.date()
    days = (expiry_date - today).days
    if days < 0:
        return "expired"
    if days <= 90:
        return "expiring_soon"
    return "valid"


def expiry_warning(expiry: Any, today: date | None = None) -> str:
    today = today or date.today()
    status = expiry_status(expiry, today)
    if status == "without_expiry":
        return "⚪ Χωρίς ημερομηνία λήξης"
    expiry_dt = pd.to_datetime(clean(expiry), errors="coerce").date()
    days = (expiry_dt - today).days
    if status == "expired":
        return f"🔴 Έληξε στις {expiry_dt:%Y-%m-%d}"
    if status == "expiring_soon":
        return f"🟠 Λήγει σε {days} ημέρες ({expiry_dt:%Y-%m-%d})"
    return f"🟢 Ισχύει έως {expiry_dt:%Y-%m-%d}"


def add_expiry_columns(stock: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    output = stock.copy()
    if "ExpiryDate" not in output.columns:
        output["ExpiryDate"] = ""
    output["ExpiryStatus"] = output["ExpiryDate"].map(lambda value: expiry_status(value, today))
    output["ExpiryWarning"] = output["ExpiryDate"].map(lambda value: expiry_warning(value, today))
    output["Semester"] = output["ExpiryDate"].map(expiry_semester)
    return output


def expiry_reports(stock: pd.DataFrame, today: date | None = None) -> dict[str, pd.DataFrame]:
    today = today or date.today()
    stock = add_expiry_columns(stock, today)
    expiry_dates = pd.to_datetime(stock["ExpiryDate"].replace("", pd.NA), errors="coerce").dt.date
    in_30 = expiry_dates.notna() & (expiry_dates >= today) & (expiry_dates <= today + pd.Timedelta(days=30))
    in_90 = expiry_dates.notna() & (expiry_dates >= today) & (expiry_dates <= today + pd.Timedelta(days=90))
    return {
        "expired products": stock[stock["ExpiryStatus"].eq("expired")],
        "expiring in 30 days": stock[in_30],
        "expiring in 90 days": stock[in_90],
        "A εξάμηνο": stock[stock["Semester"].str.startswith("A εξάμηνο", na=False)],
        "B εξάμηνο": stock[stock["Semester"].str.startswith("B εξάμηνο", na=False)],
        "products without expiry date": stock[stock["ExpiryStatus"].eq("without_expiry")],
    }


def search_stock(stock: pd.DataFrame, query: str) -> tuple[pd.DataFrame, str]:
    query = clean(query).lower()
    if not query or stock.empty:
        return stock, ""
    searchable = ["Προϊόν", "Μάρκα", "Barcode", "GTIN", "PCCode", "SerialNumber", "LotNumber", "QRRawData", "DataMatrixRawData", "CodeValue", "Κατηγορία"]
    mask = pd.Series(False, index=stock.index)
    for column in searchable:
        mask |= stock[column].astype(str).str.lower().str.contains(query, na=False, regex=False)
    matches = stock[mask]
    if not matches.empty:
        return matches, "Βρέθηκε με όνομα, μάρκα, barcode, GTIN, PC, SN, lot ή raw QR/DataMatrix"
    return stock.iloc[0:0], "Δεν βρέθηκε αποτέλεσμα"


def to_img(file):
    try:
        pil_image = Image.open(file).convert("RGB")
        pil_image = ImageOps.exif_transpose(pil_image)
        return np.array(pil_image)
    except Exception:
        data = np.frombuffer(file.getvalue(), np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            return None
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)



def file_hash(file) -> str:
    if file is None:
        return ""
    return hashlib.sha256(file.getvalue()).hexdigest()


def init_analysis_state() -> None:
    defaults = {
        "front_image_hash": "",
        "back_image_hash": "",
        "barcode_result": {"type": "Barcode", "value": "", "debug": {}},
        "greek_lookup_debug": {},
        "front_ocr_result": {"fields": {}, "lines": [], "debug": {}},
        "back_ocr_result": {"fields": {}, "lines": [], "debug": {}},
        "parsed_product_fields": {},
        "analysis_cache": {},
        "analysis_ran": False,
        "analysis_timed_out": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _empty_ocr_debug() -> dict[str, Any]:
    return {
        "ocr": tesseract_status(),
        "language": "ell+eng",
        "psm_modes": [6, 11],
        "attempts": [],
        "errors": [],
        "raw_values": [],
        "raw_text": "",
        "variant_used": "",
        "selected_candidate": "",
        "variant_results": [],
        "timed_out": False,
    }

def decoder_status() -> dict[str, str]:
    return {
        "pyzbar": "available" if pyzbar_decode else f"failed: {PYZBAR_IMPORT_ERROR}",
        "opencv_barcode": "available" if hasattr(cv2, "barcode") else "failed: cv2.barcode is not available",
        "opencv_qr": "available" if hasattr(cv2, "QRCodeDetector") else "failed: cv2.QRCodeDetector is not available",
    }


def barcode_variants(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    variants = [("original_rgb", image), ("grayscale", gray)]
    for scale in (2, 3):
        variants.append((f"resized_{scale}x", cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)))
    _, thresholded = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(("thresholded", thresholded))
    return variants


def classify_pyzbar_type(kind: str) -> str:
    mapping = {"QRCODE": "QR", "DATAMATRIX": "DataMatrix", "EAN13": "EAN-13", "EAN8": "EAN-8", "CODE128": "CODE128"}
    return mapping.get(kind, "Other")


def barcode_checksum_status(code_type: str, value: str) -> str:
    digits = clean(value)
    if code_type in {"EAN-8", "EAN-13"}:
        return "valid" if is_valid_gtin_check_digit(digits) else "invalid"
    return "not_applicable"


def extract_gs1_gtin(value: str) -> str:
    parsed = parse_machine_readable_fields(value)
    return clean(parsed.get("gtin", ""))


def select_barcode_candidate(candidates: list[dict[str, str]]) -> dict[str, Any]:
    normalized = []
    seen = set()
    for candidate in candidates:
        value = clean(candidate.get("value", ""))
        code_type = clean(candidate.get("type", "Other"))
        checksum = barcode_checksum_status(code_type, value)
        gtin = extract_gs1_gtin(value) if code_type == "DataMatrix" else ""
        if code_type in {"EAN-8", "EAN-13"} and checksum != "valid":
            continue
        key = (code_type, value, gtin)
        if value and key not in seen:
            seen.add(key)
            normalized.append({**candidate, "type": code_type, "value": value, "gtin": gtin, "checksum": checksum})
    def rank(item: dict[str, str]) -> int:
        if item.get("type") == "EAN-13":
            return 0
        if item.get("type") == "EAN-8":
            return 1
        if item.get("type") == "DataMatrix" and item.get("gtin"):
            return 2
        if item.get("type") in {"CODE128", "QR"}:
            return 3
        return 9
    normalized.sort(key=rank)
    selected = normalized[0] if normalized else {"type": "Barcode", "value": "", "gtin": "", "checksum": "not_detected"}
    return {"selected": selected, "candidates": normalized, "ambiguous": len(normalized) > 1 and rank(normalized[0]) == rank(normalized[1])}


def decode_with_pyzbar(image: np.ndarray) -> list[tuple[str, str, str]]:
    if pyzbar_decode is None:
        raise RuntimeError(f"pyzbar unavailable: {PYZBAR_IMPORT_ERROR}")
    symbols = None
    if ZBarSymbol is not None:
        wanted = ["EAN13", "EAN8", "CODE128", "QRCODE", "DATAMATRIX"]
        symbols = [getattr(ZBarSymbol, name) for name in wanted if hasattr(ZBarSymbol, name)]
    decoded = pyzbar_decode(Image.fromarray(image), symbols=symbols) if symbols else pyzbar_decode(Image.fromarray(image))
    values = []
    for item in decoded:
        raw = item.data.decode("utf-8", errors="replace")
        value = raw.strip()
        if value:
            values.append((classify_pyzbar_type(item.type), value, item.type))
    return values


def detect_code(front=None, back=None) -> tuple[str, str, dict[str, Any]]:
    debug: dict[str, Any] = {"decoders": decoder_status(), "attempts": [], "errors": [], "raw_values": []}
    collected: list[dict[str, str]] = []
    # Back/second photo is intentionally the primary source for product identity. Front is only a rescue path.
    for source, image in [("back", back), ("front", front)]:
        if image is None:
            continue
        for variant_name, variant in barcode_variants(image):
            try:
                values = decode_with_pyzbar(variant)
                debug["attempts"].append(f"pyzbar:{source}:{variant_name}:ok:{len(values)}")
                for detected_type, value, raw_type in values:
                    item = {"decoder": "pyzbar", "source": source, "variant": variant_name, "type": detected_type, "raw_type": raw_type, "value": value}
                    debug["raw_values"].append(item)
                    collected.append(item)
            except Exception as exc:
                debug["attempts"].append(f"pyzbar:{source}:{variant_name}:failed")
                debug["errors"].append(f"pyzbar {source} {variant_name}: {exc}")

            try:
                detector = cv2.barcode.BarcodeDetector()
                bgr = cv2.cvtColor(variant, cv2.COLOR_RGB2BGR) if variant.ndim == 3 else variant
                ok, values, _, _ = detector.detectAndDecode(bgr)
                found = [clean(v) for v in values] if ok and values is not None else []
                debug["attempts"].append(f"opencv_barcode:{source}:{variant_name}:ok:{len(found)}")
                for value in found:
                    if value:
                        code_type = "EAN-13" if value.isdigit() and len(value) == 13 else "EAN-8" if value.isdigit() and len(value) == 8 else "CODE128"
                        item = {"decoder": "opencv_barcode", "source": source, "variant": variant_name, "type": code_type, "value": value}
                        debug["raw_values"].append(item)
                        collected.append(item)
            except Exception as exc:
                debug["attempts"].append(f"opencv_barcode:{source}:{variant_name}:failed")
                debug["errors"].append(f"opencv_barcode {source} {variant_name}: {exc}")

            try:
                value, _, _ = cv2.QRCodeDetector().detectAndDecode(variant)
                value = clean(value)
                debug["attempts"].append(f"opencv_qr:{source}:{variant_name}:ok:{1 if value else 0}")
                if value:
                    item = {"decoder": "opencv_qr", "source": source, "variant": variant_name, "type": "QR", "value": value}
                    debug["raw_values"].append(item)
                    collected.append(item)
            except Exception as exc:
                debug["attempts"].append(f"opencv_qr:{source}:{variant_name}:failed")
                debug["errors"].append(f"opencv_qr {source} {variant_name}: {exc}")
        if collected and source == "back":
            break
    selection = select_barcode_candidate(collected)
    debug.update(selection)
    selected = selection["selected"]
    return selected.get("type", "Barcode"), selected.get("gtin") or selected.get("value", ""), debug


def tesseract_status() -> dict[str, str]:
    executable = shutil.which("tesseract")
    if pytesseract is None:
        return {"available": "no", "reason": f"pytesseract import failed: {PYTESSERACT_IMPORT_ERROR}"}
    if not executable:
        return {"available": "no", "reason": "tesseract executable was not found in PATH"}
    return {"available": "yes", "executable": executable}


def _crop_to_content(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 40, 120)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = gray.shape[:2]
    min_area = h * w * 0.08
    candidates = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= min_area]
    if not candidates:
        mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        points = cv2.findNonZero(mask)
        if points is None:
            return image
        x, y, bw, bh = cv2.boundingRect(points)
    else:
        x, y, bw, bh = max(candidates, key=lambda box: box[2] * box[3])
    pad = max(8, int(min(w, h) * 0.02))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    if (x1 - x0) < w * 0.25 or (y1 - y0) < h * 0.25:
        return image
    return image[y0:y1, x0:x1]


def _clahe(gray: np.ndarray) -> np.ndarray:
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)


def _sharpen(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.0)
    return cv2.addWeighted(gray, 1.6, blurred, -0.6, 0)


def _adaptive_threshold(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11)


def _otsu_threshold(gray: np.ndarray) -> np.ndarray:
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def _central_product_crop(pil: Image.Image) -> Image.Image:
    width, height = pil.size
    if width < 40 or height < 40:
        return pil
    left = int(width * 0.08)
    right = int(width * 0.92)
    top = int(height * 0.12)
    bottom = int(height * 0.88)
    return pil.crop((left, top, right, bottom))


def _base_ocr_image(image: np.ndarray) -> np.ndarray:
    pil = ImageOps.exif_transpose(Image.fromarray(image)).convert("RGB")
    pil = _central_product_crop(pil)
    if pil.width > 1600:
        new_height = max(1, int(pil.height * (1600 / pil.width)))
        pil = pil.resize((1600, new_height), Image.Resampling.LANCZOS)
    pil = ImageOps.grayscale(pil)
    pil = ImageEnhance.Contrast(pil).enhance(1.35)
    return np.array(pil)


def _ocr_attempts(image: np.ndarray) -> list[tuple[str, np.ndarray, int]]:
    base_gray = _base_ocr_image(image)
    return [("front_fast_single_pass", base_gray, 6), ("front_fast_single_pass", base_gray, 11)][:MAX_FRONT_OCR_CALLS]


def _expiry_ocr_attempts(image: np.ndarray) -> list[tuple[str, np.ndarray, int]]:
    pil = ImageOps.exif_transpose(Image.fromarray(image)).convert("L")
    pil = ImageEnhance.Contrast(pil).enhance(1.8)
    pil = pil.resize((pil.width * 2, pil.height * 2), Image.Resampling.LANCZOS)
    gray = np.array(pil)
    return [
        ("back_expiry_gray_2x", gray, 6),
        ("back_expiry_clahe_2x", _clahe(gray), 6),
        ("back_expiry_threshold_2x", _otsu_threshold(_clahe(gray)), 6),
        ("back_expiry_sparse_2x", _sharpen(_clahe(gray)), 11),
    ][:MAX_BACK_EXPIRY_OCR_CALLS]


def detect_back_expiry_ocr(image: np.ndarray | None, image_hash: str = "") -> tuple[dict[str, str], list[str], dict[str, Any]]:
    debug = _empty_ocr_debug()
    debug.update({"language": "eng", "psm_modes": [6, 11], "image_hash": image_hash, "ocr_kind": "back_expiry_only"})
    if image is None or debug["ocr"].get("available") != "yes":
        return {"expiry_date": ""}, [], debug
    whitelist = "0123456789/-. EXPIRYEXPΛΗΞΗ: "
    best_text = ""
    lines: list[str] = []
    for variant_name, variant, psm in _expiry_ocr_attempts(image):
        label = f"{variant_name}_psm{psm}"
        try:
            config = f"--psm {psm} -c tessedit_char_whitelist={whitelist}"
            text = pytesseract.image_to_string(Image.fromarray(variant), lang="eng", config=config, timeout=BACK_OCR_TIMEOUT_SECONDS)
            variant_lines = [clean(line) for line in text.splitlines() if clean(line)]
            debug["attempts"].append(f"tesseract:{label}:ok:{len(variant_lines)}")
            debug["variant_results"].append({"variant": variant_name, "psm": psm, "raw_text": text})
            debug["raw_values"].extend(variant_lines)
            if len(text) > len(best_text):
                best_text = text
                lines = variant_lines
            fields = extract_back_fields(text)
            if fields.get("expiry_date"):
                debug.update({"raw_text": text, "variant_used": label, "selected_candidate": fields["expiry_date"]})
                return {"expiry_date": fields["expiry_date"]}, variant_lines, debug
        except RuntimeError as exc:
            debug["attempts"].append(f"tesseract:{label}:timeout")
            debug["errors"].append(f"tesseract {label}: {exc}")
            debug["timed_out"] = True
            break
        except Exception as exc:
            debug["attempts"].append(f"tesseract:{label}:failed")
            debug["errors"].append(f"tesseract {label}: {exc}")
    fields = extract_back_fields(best_text)
    debug.update({"raw_text": best_text, "selected_candidate": fields.get("expiry_date", "")})
    return {"expiry_date": fields.get("expiry_date", "")}, list(dict.fromkeys(lines)), debug


def _has_useful_alphabetic_text(text: str) -> bool:
    alpha = len(re.findall(r"[A-Za-zΑ-Ωα-ω]", text))
    words = re.findall(r"[A-Za-zΑ-Ωα-ω]{3,}", text)
    return alpha >= 8 and len(words) >= 2


def _ocr_text(variant: np.ndarray, psm: int, timeout: int = 8) -> str:
    pil = Image.fromarray(variant)
    config = f"--psm {psm}"
    return pytesseract.image_to_string(pil, lang="ell+eng", config=config, timeout=timeout)


def _ocr_score(text: str, words: list[dict[str, Any]], avg_conf: float | None) -> tuple[int, float, int]:
    alpha = len(re.findall(r"[A-Za-zΑ-Ωα-ω]", text))
    valid_words = [w["text"] for w in words if len(re.findall(r"[A-Za-zΑ-Ωα-ω]", w["text"])) >= 2]
    numeric_penalty = len(re.findall(r"\d", text))
    return (alpha + len(valid_words) * 8 - numeric_penalty // 3, avg_conf or 0.0, len(valid_words))


def _is_descriptive_line(line: str) -> bool:
    lowered = line.lower()
    excluded = [
        "tablet", "capsule", "δισκ", "καψ", "φαρμα", "εταιρ", "manufacturer",
        "ltd", "ae", "a.e.", "ενδει", "χρήση", "περιέχει", "σύνθεση", "expiry", "exp",
    ]
    return any(token in lowered for token in excluded)


def extract_front_fields(lines: list[str], words: list[dict[str, Any]]) -> dict[str, Any]:
    unique = list(dict.fromkeys([clean(line) for line in lines if clean(line)]))
    joined = "\n".join(unique)
    strength = ""
    dosage_form = ""
    strength_match = re.search(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|μg|g|ml|iu|%)\b", joined, re.I)
    if strength_match:
        strength = strength_match.group(0)
    form_match = re.search(r"\b(tablets?|tabs?|capsules?|caps?|syrup|cream|spray|drops|δισκ\w*|καψ\w*|σιρόπι|κρέμα)\b", joined, re.I)
    if form_match:
        dosage_form = form_match.group(0)
    scored = []
    for idx, line in enumerate(unique[:12]):
        if re.search(r"\b(?:exp|lot|sn|pc)\b|\d{1,2}[./-]\d{2,4}|\b\d{8,}\b", line, re.I):
            continue
        if re.fullmatch(r"[\d\s.,/:-]+(?:mg|mcg|μg|g|ml|iu|%)?", line, re.I):
            continue
        if _is_descriptive_line(line):
            continue
        alpha = len(re.findall(r"[A-Za-zΑ-Ωα-ω]", line))
        if alpha < 3:
            continue
        upper_bonus = 25 if line == line.upper() else 0
        matching = [w for w in words if w["text"] in line]
        top = min((w["top"] for w in matching), default=idx * 100)
        avg_conf = sum(w["conf"] for w in matching if w["conf"] >= 0) / max(1, len([w for w in matching if w["conf"] >= 0])) if matching else 0
        score = 180 - idx * 12 - top / 20 + upper_bonus + alpha * 2 + avg_conf / 3
        scored.append((score, line, avg_conf))
    scored.sort(reverse=True)
    product_name = scored[0][1] if scored else ""
    brand = product_name.split()[0] if product_name else ""
    fields = normalize_product_fields({"product_name": product_name, "brand": brand, "strength": strength, "dosage_form": dosage_form})
    fields["candidate"] = product_name
    return fields


def detect_product_name(image, deadline: float | None = None) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    debug = _empty_ocr_debug()
    if image is None or debug["ocr"].get("available") != "yes":
        return {}, [], debug
    best: tuple[tuple[int, float, int], str, int, str, list[dict[str, Any]], list[str], float | None] | None = None
    for variant_name, variant, psm in _ocr_attempts(image):
        if deadline is not None and time.monotonic() >= deadline:
            debug["timed_out"] = True
            debug["errors"].append("ocr deadline exceeded")
            break
        label = f"{variant_name}_psm{psm}"
        try:
            remaining = max(1, int(deadline - time.monotonic())) if deadline is not None else 8
            text = _ocr_text(variant, psm, timeout=min(8, remaining))
            words: list[dict[str, Any]] = []
            avg_conf = None
            variant_lines = [clean(line) for line in text.splitlines() if clean(line)]
            empty = not clean(text)
            score = _ocr_score(text, words, avg_conf)
            debug["attempts"].append(f"tesseract:{label}:ok:{len(variant_lines)}:empty={empty}:conf={avg_conf}")
            debug["variant_results"].append({"variant": variant_name, "psm": psm, "empty": empty, "score": score, "raw_text": text})
            debug["raw_values"].extend(variant_lines)
            if best is None or score > best[0]:
                best = (score, variant_name, psm, text, words, variant_lines, avg_conf)
            if _has_useful_alphabetic_text(text):
                break
        except RuntimeError as exc:
            debug["attempts"].append(f"tesseract:{label}:timeout")
            debug["errors"].append(f"tesseract {label}: {exc}")
            debug["timed_out"] = True
            break
        except Exception as exc:
            debug["attempts"].append(f"tesseract:{label}:failed")
            debug["errors"].append(f"tesseract {label}: {exc}")
    if best is None:
        return {}, [], debug
    _, variant_name, psm, text, words, lines, avg_conf = best
    fields = extract_front_fields(lines, words)
    debug.update({"raw_text": text, "variant_used": f"{variant_name}_psm{psm}", "selected_candidate": fields.get("candidate", "")})
    unique_lines = list(dict.fromkeys(lines))
    return fields, unique_lines, debug


def run_photo_analysis(front_image, back_image, front_hash: str, back_hash: str) -> None:
    cache = st.session_state.analysis_cache
    cache_key = f"{front_hash}:{back_hash}"
    if cache_key in cache:
        cached = cache[cache_key]
        st.session_state.barcode_result = cached["barcode_result"]
        st.session_state.front_ocr_result = cached["front_ocr_result"]
        st.session_state.back_ocr_result = cached["back_ocr_result"]
        st.session_state.parsed_product_fields = cached["parsed_product_fields"]
        st.session_state.front_image_hash = front_hash
        st.session_state.back_image_hash = back_hash
        st.session_state.analysis_ran = True
        st.session_state.analysis_timed_out = False
        return

    progress = st.progress(0, text="Reading barcode")
    detected_type, detected_code, barcode_debug = detect_code(front_image, back_image)
    st.session_state.barcode_result = {"type": detected_type, "value": detected_code, "debug": barcode_debug}

    progress.progress(35, text="Skipping front OCR")
    front_fields, front_lines, front_debug = {}, [], _empty_ocr_debug()
    front_debug["skipped"] = "front_image_ocr_requires_manual_entry"
    st.session_state.front_ocr_result = {"fields": front_fields, "lines": front_lines, "debug": front_debug}

    progress.progress(70, text="Reading back expiry")
    back_fields: dict[str, str] = {"pc_code": "", "serial_number": "", "lot_number": "", "expiry_date": ""}
    back_lines: list[str] = []
    back_debug = _empty_ocr_debug()
    if back_image is not None:
        expiry_fields, back_lines, back_debug = detect_back_expiry_ocr(back_image, back_hash)
        back_fields.update(expiry_fields)
    else:
        back_debug["skipped"] = "no_back_image"
    st.session_state.back_ocr_result = {"fields": back_fields, "lines": back_lines, "debug": back_debug}
    st.session_state.parsed_product_fields = {**front_fields, **back_fields}
    st.session_state.front_image_hash = front_hash
    st.session_state.back_image_hash = back_hash
    st.session_state.analysis_ran = True
    st.session_state.analysis_timed_out = bool(front_debug.get("timed_out") or back_debug.get("timed_out"))
    cache[cache_key] = {
        "barcode_result": st.session_state.barcode_result,
        "front_ocr_result": st.session_state.front_ocr_result,
        "back_ocr_result": st.session_state.back_ocr_result,
        "parsed_product_fields": st.session_state.parsed_product_fields,
    }
    progress.progress(100, text="Finished")

def extract_back_fields(text: str) -> dict[str, str]:
    fields = {"pc_code": "", "serial_number": "", "lot_number": "", "expiry_date": ""}
    patterns = {
        "pc_code": r"(?:\bPC\b|P\.?C\.?|Product\s*Code)[:\s-]*([A-Z0-9-]{2,})",
        "serial_number": r"(?:\bSN\b|S/N|Serial(?:\s*Number)?)[:\s-]*([A-Z0-9-]{2,})",
        "lot_number": r"(?:\bLOT\b|Lot|Batch)[:\s-]*([A-Z0-9-]{2,})",
        "expiry_date": r"(?:\bEXP\b|Expiry|ΛΗΞΗ)[:\s-]*(\d{1,2}\s+\d{4}|\d{1,2}[./-]\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            value = clean(match.group(1))
            if key == "expiry_date":
                try:
                    fields[key] = parse_expiry_date(value)
                except InventoryError:
                    fields[key] = ""
            else:
                fields[key] = value
    if not fields["expiry_date"]:
        candidates = find_expiry_candidates(text)
        if candidates:
            fields["expiry_date"] = candidates[0]
    return fields


def _valid_expiry(year: int, month: int, day: int) -> str:
    if year < MIN_VALID_EXPIRY_YEAR:
        raise InventoryError("Η ημερομηνία λήξης είναι υπερβολικά παλιά.")
    parsed = date(year, month, day)
    return parsed.isoformat()


def parse_expiry_date(value: str) -> str:
    value = clean(value)
    if not value:
        return ""
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", value)
    if match:
        year = 2000 + int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3)) or calendar.monthrange(year, month)[1]
        return _valid_expiry(year, month, day)
    match = re.fullmatch(r"(\d{1,2})\s+(\d{4})", value)
    if match:
        month, year = int(match.group(1)), int(match.group(2))
        return _valid_expiry(year, month, calendar.monthrange(year, month)[1])
    match = re.fullmatch(r"(\d{1,2})[./-](\d{4})", value)
    if match:
        month, year = int(match.group(1)), int(match.group(2))
        return _valid_expiry(year, month, calendar.monthrange(year, month)[1])
    match = re.fullmatch(r"(\d{4})[./-](\d{1,2})(?:[./-](\d{1,2}))?", value)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        day = int(match.group(3)) if match.group(3) else calendar.monthrange(year, month)[1]
        return _valid_expiry(year, month, day)
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", value)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000
        return _valid_expiry(year, month, day)
    raise InventoryError("Η ημερομηνία λήξης δεν διαβάζεται. Χρησιμοποίησε DD/MM/YYYY ή MM/YYYY.")


def find_expiry_candidates(text: str) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    pattern = r"\d{1,2}\s+\d{4}|\d{1,2}[./-]\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}"
    for match in re.finditer(pattern, text):
        try:
            parsed = parse_expiry_date(match.group(0))
        except Exception:
            continue
        context = text[max(0, match.start() - 25):match.start()].upper()
        score = 0 if re.search(r"EXP|EXPIRY|ΛΗΞΗ", context) else 1
        candidates.append((score, match.start(), parsed))
    return list(dict.fromkeys([item[2] for item in sorted(candidates)]))


def parse_machine_readable_fields(raw_value: str) -> dict[str, str]:
    raw = str(raw_value or "").strip()
    normalized = raw.replace("(", "").replace(")", "")
    fields = {"pc_code": "", "gtin": "", "lot_number": "", "serial_number": "", "expiry_date": ""}
    idx = 0
    fixed = {"01": ("gtin", 14), "17": ("expiry_date", 6)}
    variable = {"10": "lot_number", "21": "serial_number"}
    while idx < len(normalized):
        ai = normalized[idx:idx + 2]
        if ai in fixed:
            key, length = fixed[ai]
            value = normalized[idx + 2:idx + 2 + length]
            if len(value) == length:
                fields[key] = parse_expiry_date(value) if key == "expiry_date" else value
            idx += 2 + length
            continue
        if ai in variable:
            next_positions = [
                pos for marker in fixed | variable
                if (pos := normalized.find(marker, idx + 2)) > idx + 2
            ]
            end = min(next_positions) if next_positions else len(normalized)
            fields[variable[ai]] = normalized[idx + 2:end].strip("\x1d")
            idx = end
            continue
        idx += 1
    text_fields = extract_back_fields(raw)
    for key, value in text_fields.items():
        fields[key] = fields.get(key) or value
    return fields


def parse_gs1_datamatrix(raw_value: str) -> dict[str, str]:
    return {key: value for key, value in parse_machine_readable_fields(raw_value).items() if key != "pc_code"}


def lookup_local_database(stock: pd.DataFrame, code: str, parsed: dict[str, str] | None = None) -> dict[str, Any] | None:
    parsed = parsed or {}
    if stock.empty:
        return None
    terms = [clean(code), clean(parsed.get("gtin", ""))]
    for term in [t for t in terms if t and t.isdigit()]:
        matches = stock[
            stock["Barcode"].astype(str).str.strip().eq(term)
            | stock["GTIN"].astype(str).str.strip().eq(term)
            | (
                stock["CodeType"].astype(str).isin(["Barcode", "GTIN"])
                & stock["CodeValue"].astype(str).str.strip().eq(term)
            )
        ]
        if not matches.empty:
            row = matches.iloc[0].to_dict()
            return normalize_product_fields({
                "product_name": row.get("Προϊόν", ""),
                "brand": row.get("Μάρκα", ""),
                "category": row.get("Κατηγορία", ""),
                "strength": row.get("Strength", ""),
                "dosage_form": row.get("DosageForm", ""),
                "barcode": row.get("Barcode", ""),
                "gtin": row.get("GTIN", ""),
            }) | {
                "stock": row.get("Σύνολο", 0),
                "local": True,
            }
    return None


def lookup_traceability_exact(stock: pd.DataFrame, pc_code: str = "", serial_number: str = "") -> pd.DataFrame:
    if stock.empty:
        return stock.iloc[0:0]
    mask = pd.Series(False, index=stock.index)
    if clean(pc_code):
        mask |= stock["PCCode"].astype(str).str.strip().eq(clean(pc_code))
    if clean(serial_number):
        mask |= stock["SerialNumber"].astype(str).str.strip().eq(clean(serial_number))
    return stock[mask]


GREEK_PROVIDER_DOMAINS = [
    "skroutz.gr",
    "eof.gr",
    "galinos.gr",
    "ofarmakopoiosmou.gr",
    "pharmacy295.gr",
    "vita4you.gr",
]


def _extract_jsonld_product(html: str) -> dict[str, Any]:
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        try:
            payload = requests.models.complexjson.loads(match.group(1).strip())
        except Exception:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            graph = item.get("@graph", []) if isinstance(item, dict) else []
            for node in ([item] if isinstance(item, dict) else []) + [g for g in graph if isinstance(g, dict)]:
                kind = node.get("@type", "")
                kinds = kind if isinstance(kind, list) else [kind]
                if "Product" in kinds:
                    brand = node.get("brand", "")
                    if isinstance(brand, dict):
                        brand = brand.get("name", "")
                    return {"product_name": node.get("name", ""), "brand": brand, "barcode": node.get("gtin13", "") or node.get("gtin", "")}
    return {}


def _greek_search_urls(code: str, product_name: str = "") -> list[tuple[str, str]]:
    query = clean(code) or clean(product_name)
    if not query:
        return []
    quoted = requests.utils.quote(query)
    return [
        ("skroutz.gr", f"https://www.skroutz.gr/search?keyphrase={quoted}"),
        ("galinos.gr", f"https://www.galinos.gr/web/drugs/main/search?q={quoted}"),
        ("ofarmakopoiosmou.gr", f"https://www.ofarmakopoiosmou.gr/el/search?search={quoted}"),
        ("pharmacy295.gr", f"https://www.pharmacy295.gr/search?controller=search&s={quoted}"),
        ("vita4you.gr", f"https://www.vita4you.gr/catalogsearch/result/?q={quoted}"),
        ("eof.gr", f"https://www.eof.gr/?s={quoted}"),
    ]


def _lookup_greek_provider(domain: str, search_url: str, code: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    debug = {"provider": domain, "error": "", "count": 0}
    try:
        response = requests.get(search_url, timeout=4, headers={"User-Agent": "Mozilla/5.0 ApothikiMobile/1.0"})
        response.raise_for_status()
        html = response.text
        if "captcha" in html.lower():
            debug["error"] = "blocked"
            return [], debug
        product = _extract_jsonld_product(html)
        if not product:
            title = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
            product = {"product_name": re.sub(r"\s+", " ", title.group(1)).strip() if title else ""}
        if not clean(product.get("product_name", "")):
            debug["count"] = 0
            return [], debug
        product.update({"provider": domain, "barcode": clean(code) if len(clean(code)) in {8, 13} else product.get("barcode", ""), "gtin": clean(code) if len(clean(code)) in {8, 13, 14} else ""})
        normalized = normalize_product_fields(product) | {"provider": domain, "local": False}
        debug["count"] = 1
        return [normalized], debug
    except requests.RequestException as exc:
        debug["error"] = f"connection: {exc}"
    except Exception as exc:
        debug["error"] = f"parsing: {exc}"
    return [], debug


def online_lookup_candidates(code: str, product_name: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    attempts = []
    for domain, url in _greek_search_urls(code, product_name):
        found, info = _lookup_greek_provider(domain, url, code)
        attempts.append(info)
        candidates.extend(found)
        if len(candidates) >= 3:
            break
    return candidates[:3], {"attempted": attempts, "total_results": len(candidates[:3])}


def merge_lookup_results(results: list[dict[str, Any] | None]) -> dict[str, Any]:
    merged = {"product_name": "", "brand": "", "category": "", "strength": "", "dosage_form": "", "provider": ""}
    providers = []
    for result in [r for r in results if r]:
        providers.append(result.get("provider", ""))
        normalized = normalize_product_fields(result)
        for key in ["product_name", "brand", "category", "strength", "dosage_form"]:
            if not clean(merged[key]) and clean(normalized.get(key, "")):
                merged[key] = normalized[key]
    merged["provider"] = ", ".join([provider for provider in providers if provider])
    return merged


@st.cache_resource(show_spinner=False)
def worksheet():
    if "gcp_service_account" not in st.secrets:
        st.error("Λείπουν τα Streamlit Secrets: gcp_service_account.")
        st.stop()
    try:
        info = dict(st.secrets["gcp_service_account"])
        if "private_key" in info:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        creds = Credentials.from_service_account_info(info, scopes=SCOPE)
        client = gspread.authorize(creds)
        sheet_name = st.secrets.get("SHEET_NAME", SHEET_NAME)
        worksheet_name = st.secrets.get("WORKSHEET_NAME" , WS_NAME)
        sheet = client.open(sheet_name)
        ws = sheet.worksheet(worksheet_name)
        validate_and_migrate_headers(ws)
        return ws
    except (gspread.SpreadsheetNotFound, gspread.WorksheetNotFound):
        st.error(
            "Δεν βρέθηκε το Google Sheet ή το φύλλο Transactions. "
            "Δημιούργησέ τα και μοιράσου το Sheet με το service-account email."
        )
        st.stop()
    except SchemaError as exc:
        st.error(str(exc))
        st.stop()
    except Exception as exc:
        st.error(f"Σφάλμα Goodle Sheets:{type(exc).__name__}: {exc}"
                )
        st.stop()
def main():
    st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="📦", layout="wide")
    st.title("📦 Αποθήκη Φαρμακείου")
    st.caption("Barcode/QR, fallback PC/SN, αναζήτηση, OCR και ασφαλές ιστορικό κινήσεων.")

    entry_tab, search_tab, reports_tab, sales_tab, data_tab, reversal_tab = st.tabs(
        ["➕ Καταχώρηση", "🔎 Search", "⚠️ Λήξεις / Reports", "📅 Πωλήσεις", "📄 Δεδομένα", "↩️ Αναστροφή"]
    )

    with entry_tab:
        st.subheader("Καταχώρηση")
        left, right = st.columns(2)
        with left:
            front_file = st.camera_input("Φωτογραφία πρόσοψης", key="front") or st.file_uploader(
                "Ή ανέβασε πρόσοψη", ["jpg", "jpeg", "png"], key="front_up"
            )
        with right:
            back_file = st.camera_input("Φωτογραφία πίσω / Barcode / QR", key="back") or st.file_uploader(
                "Ή ανέβασε πίσω", ["jpg", "jpeg", "png"], key="back_up"
            )

        init_analysis_state()
        front_hash = file_hash(front_file)
        back_hash = file_hash(back_file)
        front_image = to_img(front_file) if front_file else None
        back_image = to_img(back_file) if back_file else None

        if st.button("Ανάλυση φωτογραφιών", type="primary", use_container_width=True, disabled=front_image is None and back_image is None):
            run_photo_analysis(front_image, back_image, front_hash, back_hash)

        barcode_result = st.session_state.barcode_result
        front_result = st.session_state.front_ocr_result
        back_result = st.session_state.back_ocr_result
        detected_type = barcode_result.get("type", "Barcode")
        detected_code = barcode_result.get("value", "")
        barcode_debug = barcode_result.get("debug", {})
        front_suggestions = front_result.get("fields", {})
        ocr_debug = front_result.get("debug", _empty_ocr_debug())
        back_fields = back_result.get("fields", {})
        back_ocr_debug = back_result.get("debug", _empty_ocr_debug())
        detected_product = front_suggestions.get("product_name", "")
        detected_brand = front_suggestions.get("brand", "")
        has_current_analysis = (
            st.session_state.analysis_ran
            and st.session_state.front_image_hash == front_hash
            and st.session_state.back_image_hash == back_hash
        )

        if (front_image is not None or back_image is not None) and not has_current_analysis:
            st.info("Ανέβηκαν φωτογραφίες. Πάτησε «Ανάλυση φωτογραφιών» για barcode/OCR.")

        if has_current_analysis and st.session_state.analysis_timed_out:
            st.warning("Η ανάλυση καθυστέρησε. Δοκίμασε πιο καθαρή φωτογραφία.")

        if has_current_analysis and detected_code:
            st.success(f"Βρέθηκε {detected_type}: {detected_code}")
        elif has_current_analysis and (front_image is not None or back_image is not None):
            st.warning("Δεν διαβάστηκε barcode. Δοκίμασε πιο κοντινή και καθαρή φωτογραφία.")

        if has_current_analysis and front_image is not None:
            st.info("Δεν τρέχει αυτόματα OCR πρόσοψης. Συμπλήρωσε ή επιβεβαίωσε τα στοιχεία χειροκίνητα.")

        if has_current_analysis and front_image is not None and front_suggestions:
            with st.expander("Επεξεργάσιμες προτάσεις από OCR πρόσοψης", expanded=True):
                product_value = front_suggestions.get("product_name", "")
                if not clean(product_value) and clean(ocr_debug.get("raw_text", "")):
                    product_value = clean(ocr_debug.get("raw_text", ""))[:240]
                suggested_product = st.text_area("OCR text / product name candidate", value=product_value, height=100)
                suggested_brand = st.text_input("Προτεινόμενη μάρκα", value=front_suggestions.get("brand", ""))
                suggested_strength = st.text_input("Προτεινόμενη περιεκτικότητα", value=front_suggestions.get("strength", ""))
                suggested_dosage_form = st.text_input("Προτεινόμενη μορφή", value=front_suggestions.get("dosage_form", ""))
                if not clean(suggested_product) and clean(ocr_debug.get("raw_text", "")):
                    st.info("Δεν βρέθηκε δομημένο όνομα προϊόντος, αλλά υπάρχει raw OCR κείμενο. Αντέγραψε ή γράψε χειροκίνητα το σωστό όνομα.")
                    st.text_area("Raw OCR για χειροκίνητη επιλογή ονόματος", value=ocr_debug.get("raw_text", ""), height=140)
                extraction_confirmed = st.checkbox("Επιβεβαιώνω τις προτάσεις πριν την αποθήκευση")
        else:
            suggested_product = detected_product if has_current_analysis else ""
            suggested_brand = detected_brand if has_current_analysis else ""
            suggested_strength = front_suggestions.get("strength", "") if has_current_analysis else ""
            suggested_dosage_form = front_suggestions.get("dosage_form", "") if has_current_analysis else ""
            extraction_confirmed = not (has_current_analysis and front_image is not None and bool(front_suggestions))

        if front_image is not None or back_image is not None:
            with st.expander("Debug OCR / Barcode", expanded=False):
                st.write("Front image hash", front_hash)
                st.write("Back image hash", back_hash)
                st.write("Used cached/current analysis", has_current_analysis)
                st.write("Decoders", barcode_debug.get("decoders"))
                st.write("Barcode attempts", barcode_debug.get("attempts"))
                st.write("Barcode failures", barcode_debug.get("errors"))
                st.write("Detected raw barcode values", barcode_debug.get("raw_values"))
                st.write("OCR availability", ocr_debug.get("ocr"))
                st.write("OCR language", ocr_debug.get("language"))
                st.write("OCR PSM modes", ocr_debug.get("psm_modes"))
                st.write("Front OCR attempts", ocr_debug.get("attempts"))
                st.write("Front OCR failures", ocr_debug.get("errors"))
                st.write("Back OCR attempts", back_ocr_debug.get("attempts"))
                st.write("Back OCR failures", back_ocr_debug.get("errors"))
                st.text_area("Best raw OCR text from front image", value=ocr_debug.get("raw_text", ""), height=160)
                st.write("Selected best variant", ocr_debug.get("variant_used"))
                st.write("Selected product name candidate", ocr_debug.get("selected_candidate"))
                variant_results = ocr_debug.get("variant_results", [])
                if variant_results:
                    st.dataframe(pd.DataFrame([
                        {
                            "variant": item.get("variant"),
                            "psm": item.get("psm"),
                            "empty_text": item.get("empty"),
                            "score": item.get("score"),
                            "chars": len(item.get("raw_text", "")),
                        }
                        for item in variant_results
                    ]), use_container_width=True)
                st.write("OCR raw values", ocr_debug.get("raw_values"))
                st.write("Back OCR extracted logistics", back_fields)
                selected_debug = barcode_debug.get("selected", {})
                st.write("Detected code type", selected_debug.get("type", detected_type))
                st.write("Detected barcode value", selected_debug.get("value", detected_code))
                st.write("Barcode checksum result", selected_debug.get("checksum", "not_applicable"))
                st.write("All barcode candidates", barcode_debug.get("candidates", []))
                st.write("Ambiguous barcode candidates", barcode_debug.get("ambiguous", False))
                st.write("Local lookup result", st.session_state.get("local_lookup_debug", "not_run"))
                st.write("Greek providers attempted", st.session_state.get("greek_lookup_debug", {}).get("attempted", []))
                st.write("Greek result count", st.session_state.get("greek_lookup_debug", {}).get("total_results", 0))

        parsed_gs1 = parse_machine_readable_fields(detected_code) if detected_type in {"QR", "DataMatrix"} and detected_code else {}
        code_type = st.selectbox(
            "Τύπος βασικού κωδικού", ["Barcode", "GTIN", "QR", "DataMatrix", "PC", "Other"],
            index=["Barcode", "GTIN", "QR", "DataMatrix", "PC", "Other"].index("Barcode" if detected_type in {"EAN-8", "EAN-13", "CODE128"} else detected_type if detected_type in ["Barcode", "GTIN", "QR", "DataMatrix", "PC", "Other"] else "Other"),
        )
        code_input = st.text_input("Barcode / QR / Other", value=detected_code)
        lookup_code = parsed_gs1.get("gtin") or (code_input if code_type in {"Barcode", "GTIN"} else "")
        stock_for_lookup = stock_table(load_data(worksheet())[0])
        local_product = lookup_local_database(stock_for_lookup, lookup_code, parsed_gs1) if clean(lookup_code) else None
        st.session_state.local_lookup_debug = "found" if local_product else "not_found" if clean(lookup_code) else "no_code"
        refresh_online = st.button("Νέα ελληνική online αναζήτηση", disabled=not clean(lookup_code) or bool(local_product))
        if clean(lookup_code) and not local_product:
            online_candidates, greek_lookup_debug = online_lookup_candidates(lookup_code, detected_product)
        else:
            online_candidates, greek_lookup_debug = [], {"attempted": [], "total_results": 0, "skipped": "local_found" if local_product else "no_code"}
        st.session_state.greek_lookup_debug = greek_lookup_debug
        online_suggestion = merge_lookup_results(online_candidates) if online_candidates else {}
        if local_product and not online_suggestion:
            st.success(f"Βρέθηκε τοπικά: {local_product.get('product_name')} | stock: {local_product.get('stock')}")
            suggested_product = local_product.get("product_name", suggested_product)
            suggested_brand = local_product.get("brand", suggested_brand)
            suggested_strength = local_product.get("strength", suggested_strength)
            suggested_dosage_form = local_product.get("dosage_form", suggested_dosage_form)
        else:
            if online_suggestion.get("provider"):
                st.info("Βρέθηκε ελληνική online πρόταση. Τα αποτελέσματα είναι προσωρινά και χρειάζονται επιβεβαίωση.")
                suggested_product = online_suggestion.get("product_name") or suggested_product
                suggested_brand = online_suggestion.get("brand") or suggested_brand
                suggested_strength = online_suggestion.get("strength") or suggested_strength
                suggested_dosage_form = online_suggestion.get("dosage_form") or suggested_dosage_form
            elif clean(lookup_code):
                st.warning("Δεν βρέθηκε ελληνική online πρόταση. Άνοιξε η χειροκίνητη καταχώρηση με προσυμπληρωμένο τον κωδικό.")

        st.caption("Αν δεν διαβάζεται το QR/DataMatrix, συμπλήρωσε PC και/ή SN. Τα πεδία είναι προαιρετικά μεμονωμένα.")
        pc_code = st.text_input("PC code (προαιρετικό)", value=parsed_gs1.get("pc_code") or back_fields.get("pc_code", "") or (code_input if code_type == "PC" else ""))
        serial_number = st.text_input("Serial Number / SN (προαιρετικό)", value=parsed_gs1.get("serial_number") or back_fields.get("serial_number", ""))
        lot_number = st.text_input("Lot number (προαιρετικό)", value=parsed_gs1.get("lot_number") or back_fields.get("lot_number", ""))
        expiry_date = st.text_input("Expiry date", value=parsed_gs1.get("expiry_date") or back_fields.get("expiry_date", ""))
        gtin = st.text_input("GTIN", value=parsed_gs1.get("gtin") or (code_input if code_type == "GTIN" else ""))
        trace_matches = lookup_traceability_exact(stock_for_lookup, pc_code, serial_number)
        if not trace_matches.empty:
            st.info(f"Βρέθηκαν {len(trace_matches)} τοπικές κινήσεις με ακριβές PC/SN (μόνο για traceability, όχι ταυτότητα προϊόντος).")
        with st.expander("Επιβεβαίωση ανίχνευσης πριν την αποθήκευση", expanded=True):
            st.write({
                "detected_barcode": code_input if code_type == "Barcode" else "",
                "detected_gtin": gtin,
                "pc_code": pc_code,
                "serial_number": serial_number,
                "lot_number": lot_number,
                "proposed_expiry_date": expiry_date,
            })
            with st.expander("Raw QR/DataMatrix debug", expanded=False):
                st.text_area("Raw decoded value", value=code_input if code_type in {"QR", "DataMatrix"} else "", height=100)
        for warning in validate_barcode_gtin(code_input if code_type == "Barcode" else "", gtin):
            st.warning(warning)
        if online_candidates:
            st.write("Ελληνικές online προτάσεις (έως 3) — η πηγή εμφανίζεται μόνο εδώ για έγκριση")
            st.dataframe(pd.DataFrame(online_candidates), use_container_width=True)
        with st.expander("Προτεινόμενο προϊόν", expanded=bool(online_suggestion.get("provider"))):
            product = st.text_input("Όνομα προϊόντος", value=suggested_product)
            brand = st.text_input("Μάρκα", value=suggested_brand)
            strength = st.text_input("Περιεκτικότητα", value=suggested_strength)
            dosage_form = st.text_input("Μορφή", value=suggested_dosage_form)
            confirmed_product = st.checkbox("Επιβεβαιώνω τα στοιχεία")
        category = st.selectbox("Κατηγορία", CATEGORIES)
        location_label = st.selectbox("Τοποθεσία", [f"{k} - {v}" for k, v in LOCATIONS.items()])
        location_id = int(location_label.split("-")[0].strip())
        movement = st.selectbox("Κίνηση", ["Παραλαβή (+)", "Πώληση (-)", "Διόρθωση (+)", "Διόρθωση (-)"])
        quantity = st.number_input("Ποσότητα", min_value=1, value=1, step=1)
        note = st.text_input("Σημείωση")

        if "pending_transaction_id" not in st.session_state:
            st.session_state.pending_transaction_id = str(uuid.uuid4())

        if st.button("💾 Αποθήκευση", use_container_width=True):
            try:
                resolved_type, code_value, barcode = resolve_identity(
                    code_type, code_input, pc_code, serial_number
                )
                if front_image is not None and front_suggestions and not extraction_confirmed:
                    raise InventoryError("Επιβεβαίωσε τις προτάσεις OCR πριν την αποθήκευση.")
                if not clean(product):
                    raise InventoryError("Βάλε όνομα προϊόντος.")
                if not confirmed_product:
                    raise InventoryError("Επιβεβαίωσε τα στοιχεία προϊόντος πριν την αποθήκευση.")
                validation_warnings = validate_barcode_gtin(barcode, gtin or parsed_gs1.get("gtin", ""))
                if validation_warnings:
                    raise InventoryError(" ".join(validation_warnings))
                normalized_product = normalize_product_fields(
                    {
                        "product_name": product,
                        "brand": brand,
                        "strength": strength,
                        "dosage_form": dosage_form,
                        "barcode": barcode,
                        "gtin": gtin or parsed_gs1.get("gtin", ""),
                    }
                )
                normalized_expiry = parse_expiry_date(expiry_date) if clean(expiry_date) else ""
                if category == "Φάρμακο" and movement in {"Παραλαβή (+)", "Διόρθωση (+)"} and not normalized_expiry:
                    st.warning("Δεν υπάρχει ημερομηνία λήξης. Συνεχίζω με κενό πεδίο για χειροκίνητη συμπλήρωση αργότερα.")
                delta = int(quantity) if movement in {"Παραλαβή (+)", "Διόρθωση (+)"} else -int(quantity)
                row = make_transaction(
                    code_type=resolved_type,
                    code_value=code_value,
                    barcode=normalized_product["barcode"],
                    pc_code=pc_code,
                    gtin=normalized_product["gtin"],
                    serial_number=serial_number,
                    lot_number=lot_number,
                    expiry_date=normalized_expiry,
                    qr_raw_data=code_input if code_type == "QR" else "",
                    datamatrix_raw_data=code_input if code_type == "DataMatrix" else "",
                    strength=normalized_product["strength"],
                    dosage_form=normalized_product["dosage_form"],
                    brand=normalized_product["brand"],
                    product=normalized_product["product_name"],
                    category=category,
                    location_id=location_id,
                    movement=movement,
                    quantity=int(quantity),
                    delta=delta,
                    note=" | ".join(filter(None, [
                        clean(note),
                        f"strength={normalized_product['strength']}" if normalized_product["strength"] else "",
                        f"dosage_form={normalized_product['dosage_form']}" if normalized_product["dosage_form"] else "",
                        f"lot_number={clean(lot_number)}" if clean(lot_number) else "",
                        f"expiry_date={clean(normalized_expiry)}" if clean(normalized_expiry) else "",
                    ])),
                    transaction_id=st.session_state.pending_transaction_id,
                )
                status = append_stock_transaction(worksheet(), row)
                if status == "duplicate":
                    st.info("Η ίδια υποβολή έχει ήδη καταχωρηθεί.")
                elif status == "compensated":
                    st.error(
                        "Εντοπίστηκε ταυτόχρονη μεταβολή stock. Η κίνηση "
                        "αντισταθμίστηκε αυτόματα και δεν εφαρμόστηκε."
                    )
                else:
                    st.success("Αποθηκεύτηκε στο Google Sheets.")
                st.session_state.pending_transaction_id = str(uuid.uuid4())
            except InventoryError as exc:
                st.error(str(exc))

    with search_tab:
        data, unknown = load_data(worksheet())
        if unknown:
            st.warning("Άγνωστες στήλες στο Sheet: " + ", ".join(unknown))
        stock = stock_table(data)
        query = st.text_input("Αναζήτηση: Barcode, QR, PC, SN, μάρκα ή όνομα")
        results, message = search_stock(stock, query)
        if message:
            st.info(message)
        if not results.empty:
            warning_counts = results["ExpiryStatus"].value_counts().to_dict() if "ExpiryStatus" in results else {}
            if warning_counts.get("expired", 0):
                st.error(f"{warning_counts['expired']} προϊόν(τα) έχουν λήξει.")
            if warning_counts.get("expiring_soon", 0):
                st.warning(f"{warning_counts['expiring_soon']} προϊόν(τα) λήγουν μέσα στις επόμενες 90 ημέρες.")
            if warning_counts.get("without_expiry", 0):
                st.info(f"{warning_counts['without_expiry']} προϊόν(τα) δεν έχουν ημερομηνία λήξης.")
            for _, row in results.head(12).iterrows():
                with st.container(border=True):
                    st.markdown(f"**{row.get('Προϊόν', '') or 'Χωρίς όνομα'}**")
                    st.caption(f"{row.get('Μάρκα', '')} | {row.get('Κατηγορία', '')} | {row.get('CodeType', '')}: {row.get('CodeValue', '')}")
                    status = row.get("ExpiryStatus", "")
                    warning = row.get("ExpiryWarning", "")
                    if status == "expired":
                        st.error(warning)
                    elif status == "expiring_soon":
                        st.warning(warning)
                    elif status == "without_expiry":
                        st.info(warning)
                    else:
                        st.success(warning)
                    st.write(
                        f"Stock: {row.get('Σύνολο', 0)} | Lot: {row.get('LotNumber', '')} | "
                        f"Εξάμηνο: {row.get('Semester', '') or '-'}"
                    )
        st.dataframe(results, use_container_width=True)

    with reports_tab:
        data, unknown = load_data(worksheet())
        if unknown:
            st.warning("Άγνωστες στήλες στο Sheet: " + ", ".join(unknown))
        stock = stock_table(data)
        reports = expiry_reports(stock)
        labels = {
            "expired products": "Expired products",
            "expiring in 30 days": "Expiring in 30 days",
            "expiring in 90 days": "Expiring in 90 days",
            "A εξάμηνο": "A εξάμηνο",
            "B εξάμηνο": "B εξάμηνο",
            "products without expiry date": "Products without expiry date",
        }
        report_key = st.selectbox("Report", list(labels), format_func=lambda key: labels[key])
        selected = reports[report_key]
        st.metric("Προϊόντα", len(selected))
        if selected.empty:
            st.info("Δεν υπάρχουν προϊόντα για αυτό το report.")
        else:
            st.dataframe(selected, use_container_width=True)

    with sales_tab:
        data, _ = load_data(worksheet())
        data = active_movements(data)
        data["Timestamp_dt"] = pd.to_datetime(data["Timestamp"], errors="coerce")
        start_col, end_col = st.columns(2)
        start = pd.to_datetime(start_col.date_input("Από"))
        end = pd.to_datetime(end_col.date_input("Έως")) + pd.Timedelta(days=1)
        period = data[(data["Timestamp_dt"] >= start) & (data["Timestamp_dt"] < end)]
        sales = period[period["DeltaQty"] < 0].copy()
        if sales.empty:
            st.info("Δεν υπάρχουν πωλήσεις ή αφαιρέσεις.")
        else:
            sales["Πωλήθηκαν"] = sales["DeltaQty"].abs()
            report = (
                sales.groupby(
                    ["CodeType", "CodeValue", "PCCode", "SerialNumber", "Μάρκα", "Προϊόν", "Τοποθεσία"],
                    dropna=False,
                )["Πωλήθηκαν"]
                .sum().reset_index().sort_values("Πωλήθηκαν", ascending=False)
            )
            st.dataframe(report, use_container_width=True)

    with data_tab:
        data, unknown = load_data(worksheet())
        if unknown:
            st.warning("Άγνωστες στήλες στο Sheet: " + ", ".join(unknown))
        st.dataframe(data, use_container_width=True)

    with reversal_tab:
        data, _ = load_data(worksheet())
        candidates = reversible_rows(data)
        if candidates.empty:
            st.info("Δεν υπάρχουν διαθέσιμες κινήσεις για αναστροφή.")
        else:
            tx_ids = candidates["TransactionId"].tolist()
            selected_id = st.selectbox(
                "Επίλεξε κίνηση",
                tx_ids,
                format_func=lambda txid: (
                    f"{candidates.loc[candidates['TransactionId'].eq(txid), 'Ημερομηνία'].iloc[0]} | "
                    f"{candidates.loc[candidates['TransactionId'].eq(txid), 'Προϊόν'].iloc[0]} | {txid}"
                ),
            )
            reason = st.text_input("Λόγος αναστροφής")
            confirm = st.checkbox("Επιβεβαιώνω την αναστροφή.")
            if st.button("↩️ Δημιουργία αναστροφής", use_container_width=True):
                if not confirm:
                    st.error("Χρειάζεται επιβεβαίωση.")
                else:
                    try:
                        original = candidates[candidates["TransactionId"].eq(selected_id)].iloc[0]
                        status = append_reversal(worksheet(), original, reason)
                        if status == "duplicate":
                            st.info("Η κίνηση έχει ήδη αναστραφεί.")
                        elif status == "compensated":
                            st.error("Η αναστροφή αντισταθμίστηκε λόγω ταυτόχρονης μεταβολής stock.")
                        else:
                            st.success("Η αναστροφή καταχωρήθηκε χωρίς διαγραφή ιστορικού.")
                    except InventoryError as exc:
                        st.error(str(exc))


if __name__ == "__main__":
    main()
