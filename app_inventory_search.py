import shutil
import uuid
from datetime import datetime
from typing import Any

import cv2
import gspread
import numpy as np
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from PIL import Image

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

SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_NAME = "Apothiki_Cloud"
WS_NAME = "Transactions"

LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}
CATEGORIES = ["Φάρμακο", "Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Άλλο"]

COLUMNS = [
    "TransactionId",
    "Timestamp",
    "Ημερομηνία",
    "CodeType",
    "CodeValue",
    "Barcode",
    "PCCode",
    "SerialNumber",
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


def deterministic_reversal_id(original_id: str) -> str:
    return f"reverse-{clean(original_id)}"


def deterministic_compensation_id(transaction_id: str) -> str:
    return f"compensation-{clean(transaction_id)}"


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
    missing = [column for column in COLUMNS if column not in headers]
    if missing:
        headers = headers + missing
        ws.update("A1", [headers])
    unknown = [header for header in headers if header not in COLUMNS]
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
        "SerialNumber",
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
    try:
        ws.append_row(
            [row.get(header, "") for header in headers],
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
    serial_number: str = "",
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
        "SerialNumber": clean(serial_number),
        "Μάρκα": clean(brand),
        "Προϊόν": clean(product),
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
            serial_number=row.get("SerialNumber", ""),
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
        serial_number=original.get("SerialNumber", ""),
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
        "CodeType", "CodeValue", "Barcode", "PCCode", "SerialNumber",
        "Μάρκα", "Προϊόν", "Κατηγορία", "Αποθήκη", "Κύριο Κτήριο",
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
        .tail(1)[identity + ["Barcode", "PCCode", "SerialNumber", "Μάρκα", "Προϊόν", "Κατηγορία"]]
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
    return (
        pivot.merge(latest, on=identity, how="left")
        .sort_values("Σύνολο", ascending=False)[output_columns]
    )


def search_stock(stock: pd.DataFrame, query: str) -> tuple[pd.DataFrame, str]:
    query = clean(query).lower()
    if not query or stock.empty:
        return stock, ""
    searchable = ["CodeValue", "Barcode", "PCCode", "SerialNumber", "Μάρκα", "Προϊόν", "Κατηγορία"]
    mask = pd.Series(False, index=stock.index)
    for column in searchable:
        mask |= stock[column].astype(str).str.lower().str.contains(query, na=False, regex=False)
    matches = stock[mask]
    if not matches.empty:
        return matches, "Βρέθηκε με κωδικό, PC, SN, μάρκα ή όνομα"
    return stock.iloc[0:0], "Δεν βρέθηκε αποτέλεσμα"


def to_img(file):
    data = np.frombuffer(file.getvalue(), np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        return None
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


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
    if kind == "QRCODE":
        return "QR"
    return "Barcode" if kind in {"EAN13", "EAN8", "CODE128", "DATAMATRIX"} else "Other"


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
        value = clean(raw)
        if value:
            values.append((classify_pyzbar_type(item.type), value, item.type))
    return values


def detect_code(front=None, back=None) -> tuple[str, str, dict[str, Any]]:
    debug: dict[str, Any] = {"decoders": decoder_status(), "attempts": [], "errors": [], "raw_values": []}
    for source, image in [("back", back), ("front", front)]:
        if image is None:
            continue
        for variant_name, variant in barcode_variants(image):
            try:
                values = decode_with_pyzbar(variant)
                debug["attempts"].append(f"pyzbar:{source}:{variant_name}:ok:{len(values)}")
                for detected_type, value, raw_type in values:
                    debug["raw_values"].append({"decoder": "pyzbar", "source": source, "variant": variant_name, "type": raw_type, "value": value})
                    return detected_type, value, debug
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
                        debug["raw_values"].append({"decoder": "opencv_barcode", "source": source, "variant": variant_name, "value": value})
                        return ("Barcode" if value.isdigit() else "Other"), value, debug
            except Exception as exc:
                debug["attempts"].append(f"opencv_barcode:{source}:{variant_name}:failed")
                debug["errors"].append(f"opencv_barcode {source} {variant_name}: {exc}")

            try:
                value, _, _ = cv2.QRCodeDetector().detectAndDecode(variant)
                value = clean(value)
                debug["attempts"].append(f"opencv_qr:{source}:{variant_name}:ok:{1 if value else 0}")
                if value:
                    debug["raw_values"].append({"decoder": "opencv_qr", "source": source, "variant": variant_name, "value": value})
                    return "QR", value, debug
            except Exception as exc:
                debug["attempts"].append(f"opencv_qr:{source}:{variant_name}:failed")
                debug["errors"].append(f"opencv_qr {source} {variant_name}: {exc}")
    return "Barcode", "", debug


def tesseract_status() -> dict[str, str]:
    executable = shutil.which("tesseract")
    if pytesseract is None:
        return {"available": "no", "reason": f"pytesseract import failed: {PYTESSERACT_IMPORT_ERROR}"}
    if not executable:
        return {"available": "no", "reason": "tesseract executable was not found in PATH"}
    return {"available": "yes", "executable": executable}


def ocr_variants(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    _, thresholded = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return [("original_rgb", image), ("grayscale", gray), ("thresholded", thresholded)]


def detect_product_name(image) -> tuple[str, list[str], dict[str, Any]]:
    debug: dict[str, Any] = {"ocr": tesseract_status(), "attempts": [], "errors": [], "raw_values": []}
    if image is None:
        return "", [], debug
    if debug["ocr"].get("available") != "yes":
        return "", [], debug
    lines: list[str] = []
    for variant_name, variant in ocr_variants(image):
        try:
            text = pytesseract.image_to_string(Image.fromarray(variant), lang="ell+eng")
            variant_lines = [clean(line) for line in text.splitlines() if clean(line)]
            debug["attempts"].append(f"tesseract:{variant_name}:ok:{len(variant_lines)}")
            debug["raw_values"].extend(variant_lines)
            lines.extend(variant_lines)
        except Exception as exc:
            debug["attempts"].append(f"tesseract:{variant_name}:failed")
            debug["errors"].append(f"tesseract {variant_name}: {exc}")
    unique_lines = list(dict.fromkeys(lines))
    return (max(unique_lines, key=len), unique_lines, debug) if unique_lines else ("", [], debug)


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
        sheet = client.open(st.secrets.get("SHEET_NAME", SHEET_NAME))
        ws = sheet.worksheet(WS_NAME)
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
    except Exception:
        st.error(
            "Αποτυχία σύνδεσης με το Google Sheets. Έλεγξε τα secrets, "
            "την κοινή χρήση του Sheet και τη σύνδεση."
        )
        st.stop()


def main():
    st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="📦", layout="wide")
    st.title("📦 Αποθήκη Φαρμακείου")
    st.caption("Barcode/QR, fallback PC/SN, αναζήτηση, OCR και ασφαλές ιστορικό κινήσεων.")

    entry_tab, search_tab, sales_tab, data_tab, reversal_tab = st.tabs(
        ["➕ Καταχώρηση", "🔎 Search", "📅 Πωλήσεις", "📄 Δεδομένα", "↩️ Αναστροφή"]
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

        front_image = to_img(front_file) if front_file else None
        back_image = to_img(back_file) if back_file else None
        detected_type, detected_code, barcode_debug = detect_code(front_image, back_image)
        detected_product, _, ocr_debug = detect_product_name(front_image)

        if detected_code:
            st.success(f"Βρέθηκε {detected_type}: {detected_code}")
        elif front_image is not None or back_image is not None:
            st.warning("Δεν διαβάστηκε barcode. Δοκίμασε πιο κοντινή και καθαρή φωτογραφία.")

        if detected_product:
            st.info(f"Πρόταση OCR: {detected_product}")
        elif front_image is not None and ocr_debug["ocr"].get("available") != "yes":
            st.warning("Το OCR δεν είναι διαθέσιμο. Δες τις λεπτομέρειες στο debug.")

        if front_image is not None or back_image is not None:
            with st.expander("Debug OCR / Barcode"):
                st.write("Decoders", barcode_debug.get("decoders"))
                st.write("Barcode attempts", barcode_debug.get("attempts"))
                st.write("Barcode failures", barcode_debug.get("errors"))
                st.write("Detected raw barcode values", barcode_debug.get("raw_values"))
                st.write("OCR availability", ocr_debug.get("ocr"))
                st.write("OCR attempts", ocr_debug.get("attempts"))
                st.write("OCR failures", ocr_debug.get("errors"))
                st.write("OCR raw values", ocr_debug.get("raw_values"))

        code_type = st.selectbox(
            "Τύπος βασικού κωδικού", ["Barcode", "QR", "Other"],
            index=["Barcode", "QR", "Other"].index(detected_type),
        )
        code_input = st.text_input("Barcode / QR / Other", value=detected_code)
        st.caption("Αν δεν διαβάζεται το QR, συμπλήρωσε PC και/ή SN. Τα πεδία είναι προαιρετικά μεμονωμένα.")
        pc_code = st.text_input("PC code (προαιρετικό)")
        serial_number = st.text_input("Serial Number / SN (προαιρετικό)")
        brand = st.text_input("Μάρκα")
        product = st.text_input("Όνομα προϊόντος", value=detected_product)
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
                if not clean(product):
                    raise InventoryError("Βάλε όνομα προϊόντος.")
                delta = int(quantity) if movement in {"Παραλαβή (+)", "Διόρθωση (+)"} else -int(quantity)
                row = make_transaction(
                    code_type=resolved_type,
                    code_value=code_value,
                    barcode=barcode,
                    pc_code=pc_code,
                    serial_number=serial_number,
                    brand=brand,
                    product=product,
                    category=category,
                    location_id=location_id,
                    movement=movement,
                    quantity=int(quantity),
                    delta=delta,
                    note=note,
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
        st.dataframe(results, use_container_width=True)

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
