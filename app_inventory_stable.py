import hashlib
import io
import uuid
from typing import Any

import pandas as pd
import streamlit as st

try:
    from streamlit_qrcode_scanner import qrcode_scanner
except Exception:
    qrcode_scanner = None

import ai_inventory
import app_inventory_search as core
import inventory_base as base_db
import inventory_csa as csa
from barcode_lookup import lookup_barcode_online
from pharmacy_catalog import catalog_dataframe as pharmacy_catalog_dataframe
from pharmacy_catalog import lookup_pharmacy_product
from starter_catalog import LAMBERTS_PRODUCTS, lookup_starter_product


LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}
DEFAULT_CATEGORY = "Άλλο"
STOCK_CACHE_TTL_SECONDS = 30
PRODUCT_CACHE_TTL_SECONDS = 60
APP_VERSION = "2026.10.02.1"
PROVIDER_BENCHMARK_CODES = ["5200421900551", "5055148400620", "033984003972"]


def clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def configured_openai_model() -> str:
    model = clean(st.secrets.get("OPENAI_MODEL", "gpt-4.1-mini"))
    # Old setup instructions used an internal Codex model name that the API
    # cannot serve. Repair that stale secret automatically.
    if model in {"gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"}:
        return "gpt-4.1-mini"
    return model or "gpt-4.1-mini"


@st.cache_resource(show_spinner=False)
def ensure_storage_once() -> bool:
    """Initialize the Google Sheets structure only once per app process."""
    core.worksheet()  # worksheet() already validates the Transactions schema.
    base_db.ensure_base_sheets(core)
    return True


@st.cache_data(ttl=PRODUCT_CACHE_TTL_SECONDS, show_spinner=False)
def read_products() -> pd.DataFrame:
    return base_db.read_sheet_df(core, "Products", base_db.PRODUCT_COLUMNS)


def local_product_by_code(code: str) -> dict[str, str] | None:
    code = clean(code)
    if not code:
        return None
    products = read_products()
    if not products.empty:
        for column in ["Barcode", "GTIN", "PC_GTIN", "DataMatrix_PC"]:
            if column not in products.columns:
                continue
            match = products[products[column].astype(str).str.strip().eq(code)]
            if not match.empty:
                row = match.iloc[0]
                return {
                    "product_name": clean(row.get("ProductName")),
                    "brand": clean(row.get("Brand")),
                    "barcode": clean(row.get("Barcode")) or code,
                    "gtin": clean(row.get("GTIN")),
                    "strength": clean(row.get("Strength")),
                    "dosage_form": clean(row.get("DosageForm")),
                    "package_size": "",
                    "category": clean(row.get("Category")) or DEFAULT_CATEGORY,
                    "source": "Δική σου επιβεβαιωμένη βάση",
                    "url": "",
                    "confidence": 1.0,
                }
    # Explicitly verified pharmacy entries take precedence over the imported
    # catalog, whose descriptions can be abbreviated or inconsistently spaced.
    product_name = lookup_starter_product(code)
    if product_name:
        attributes = core.extract_commercial_attributes(product_name)
        return {
            "product_name": product_name,
            "brand": "LAMBERTS",
            "barcode": code,
            "gtin": "",
            "strength": attributes["strength"],
            "dosage_form": attributes["dosage_form"],
            "package_size": attributes["package_size"],
            "category": "Συμπλήρωμα διατροφής",
            "source": "Κατάλογος φαρμακείου",
            "url": "",
            "confidence": 1.0,
        }
    catalog_match = lookup_pharmacy_product(code)
    if catalog_match:
        product_name = catalog_match["product_name"]
        attributes = core.extract_commercial_attributes(product_name)
        brand_match = next(
            (brand for brand in ["AVENE", "LIERAC", "LAMBERTS", "SOLGAR", "FREZYDERM", "KORRES", "VICHY", "LA ROCHE-POSAY", "BIODERMA", "FROIKA", "MUSTELA", "APIVITA", "EUCERIN"] if brand in product_name.upper()),
            "",
        )
        return {
            "product_name": product_name,
            "brand": brand_match,
            "barcode": code,
            "gtin": "",
            "strength": attributes["strength"],
            "dosage_form": attributes["dosage_form"],
            "package_size": attributes["package_size"],
            "category": DEFAULT_CATEGORY,
            "source": "Βάση προϊόντων φαρμακείου",
            "url": "",
            "confidence": 1.0,
        }
    return None


def save_inventory_item(
    *,
    code: str,
    product_name: str,
    quantity: int,
    brand: str = "",
    strength: str = "",
    dosage_form: str = "",
    expiry_date: str = "",
    lot_number: str = "",
    category: str = DEFAULT_CATEGORY,
    location_id: int = 0,
    note: str = "",
    transaction_id: str = "",
) -> None:
    code = clean(code)
    product_name = clean(product_name)
    expiry_date = core.parse_expiry_date(clean(expiry_date)) if clean(expiry_date) else ""
    if not code:
        raise ValueError("Δεν υπάρχει barcode.")
    if not product_name:
        raise ValueError("Δεν υπάρχει όνομα προϊόντος.")
    if quantity < 1:
        raise ValueError("Η ποσότητα πρέπει να είναι τουλάχιστον 1.")

    is_gtin14 = code.isdigit() and len(code) == 14
    code_type = "GTIN" if is_gtin14 else "Barcode"
    barcode = "" if is_gtin14 else code
    gtin = code if is_gtin14 else ""

    transaction = core.make_transaction(
        code_type=code_type,
        code_value=code,
        barcode=barcode,
        gtin=gtin,
        brand=brand,
        product=product_name,
        category=category or DEFAULT_CATEGORY,
        strength=strength,
        dosage_form=dosage_form,
        expiry_date=expiry_date,
        lot_number=lot_number,
        location_id=location_id,
        movement="Απογραφή / καταμέτρηση (+)",
        quantity=int(quantity),
        delta=int(quantity),
        note=note or "source=barcode_inventory; verified_by_user=true",
        transaction_id=clean(transaction_id) or None,
        movement_kind=core.NORMAL,
    )
    ws = core.worksheet()
    core.append_stock_transaction(ws, transaction)
    try:
        base_db.upsert_product_from_transaction(core, transaction)
    except Exception:
        pass
    core.invalidate_data_cache(ws)
    read_products.clear()


def edit_stock_lot(
    ws,
    original: Any,
    *,
    product_name: str,
    brand: str,
    strength: str,
    dosage_form: str,
    category: str,
    quantity: int,
    expiry_date: str = "",
    lot_number: str = "",
    location_id: int = 0,
    reason: str = "",
    edit_id: str,
) -> str:
    """Correct one saved lot by appending auditable stock movements."""
    product_name = clean(product_name)
    if not product_name:
        raise ValueError("Συμπλήρωσε το όνομα προϊόντος.")
    if int(quantity) < 0:
        raise ValueError("Η ποσότητα δεν μπορεί να είναι αρνητική.")
    if int(location_id) not in LOCATIONS:
        raise ValueError("Επίλεξε έγκυρη τοποθεσία.")
    old_quantity = int(original.get("Stock", 0))
    if old_quantity <= 0:
        raise ValueError("Η επιλεγμένη παρτίδα δεν έχει διαθέσιμο υπόλοιπο.")
    if clean(original.get("SerialNumber")) and int(quantity) > 1:
        raise ValueError("Η συσκευασία έχει σειριακό αριθμό, επομένως η ποσότητα δεν μπορεί να ξεπερνά το 1.")

    old_location = int(original.get("LocationId", 0))
    expiry_date = core.parse_expiry_date(clean(expiry_date)) if clean(expiry_date) else ""
    new_quantity = int(quantity)
    original_fields = {
        "product_name": clean(original.get("Προϊόν")),
        "brand": clean(original.get("Μάρκα")),
        "strength": clean(original.get("Strength")),
        "dosage_form": clean(original.get("DosageForm")),
        "category": clean(original.get("Κατηγορία")) or DEFAULT_CATEGORY,
        "lot_number": clean(original.get("LotNumber")),
        "expiry_date": clean(original.get("ExpiryDate")),
    }
    new_fields = {
        "product_name": product_name,
        "brand": clean(brand),
        "strength": clean(strength),
        "dosage_form": clean(dosage_form),
        "category": clean(category) or DEFAULT_CATEGORY,
        "lot_number": clean(lot_number),
        "expiry_date": expiry_date,
    }
    if (
        old_quantity == new_quantity
        and old_location == int(location_id)
        and all(new_fields[key] == value for key, value in original_fields.items())
    ):
        raise ValueError("Δεν έχει γίνει κάποια αλλαγή.")

    code_type = clean(original.get("CodeType")) or "Barcode"
    code_value = clean(original.get("CodeValue"))
    if not code_value:
        raise ValueError("Η εγγραφή δεν έχει κωδικό προϊόντος και δεν μπορεί να διορθωθεί εδώ.")
    edit_key = hashlib.sha256(clean(edit_id).encode("utf-8")).hexdigest()[:24]
    common = {
        "code_type": code_type,
        "code_value": code_value,
        "barcode": clean(original.get("Barcode")),
        "pc_code": clean(original.get("PCCode")),
        "gtin": clean(original.get("GTIN")),
        "serial_number": clean(original.get("SerialNumber")),
        "qr_raw_data": clean(original.get("QRRawData")),
        "datamatrix_raw_data": clean(original.get("DataMatrixRawData")),
    }
    edit_note = f"source=stock_edit; edit_id={edit_key}; reason={clean(reason) or 'διορθωση στοιχειων'}"
    remove_row = core.make_transaction(
        **common,
        lot_number=clean(original.get("LotNumber")),
        expiry_date=clean(original.get("ExpiryDate")),
        strength=clean(original.get("Strength")),
        dosage_form=clean(original.get("DosageForm")),
        brand=clean(original.get("Μάρκα")),
        product=clean(original.get("Προϊόν")),
        category=clean(original.get("Κατηγορία")) or DEFAULT_CATEGORY,
        location_id=old_location,
        movement="Διόρθωση stock (-)",
        quantity=old_quantity,
        delta=-old_quantity,
        note=f"{edit_note}; side=remove_previous_lot",
        transaction_id=f"stock-edit-{edit_key}-out",
        movement_kind=core.NORMAL,
    )
    add_row = core.make_transaction(
        **common,
        lot_number=new_fields["lot_number"],
        expiry_date=new_fields["expiry_date"],
        strength=new_fields["strength"],
        dosage_form=new_fields["dosage_form"],
        brand=new_fields["brand"],
        product=new_fields["product_name"],
        category=new_fields["category"],
        location_id=int(location_id),
        movement="Διόρθωση stock (+)",
        quantity=new_quantity,
        delta=new_quantity,
        note=f"{edit_note}; side=save_corrected_lot",
        transaction_id=f"stock-edit-{edit_key}-in",
        movement_kind=core.NORMAL,
    )
    corrections = [remove_row] + ([add_row] if new_quantity else [])

    fresh, _ = core.load_data(ws)
    pending = [row for row in corrections if not core.transaction_exists(fresh, row["TransactionId"])]
    # Guard against another sale or correction after the editor was opened.
    # When retrying a partially/fully written edit, deterministic transaction
    # IDs let us finish that same edit without applying its old quantity twice.
    if len(pending) == len(corrections):
        fresh_lots = csa.stock_snapshot(fresh)
        lot_identity = [
            "CodeType", "CodeValue", "Barcode", "PCCode", "GTIN", "SerialNumber", "LotNumber",
            "ExpiryDate", "QRRawData", "DataMatrixRawData", "Strength", "DosageForm",
            "Μάρκα", "Προϊόν", "Κατηγορία", "LocationId",
        ]
        if fresh_lots.empty:
            current_lot_quantity = 0
        else:
            current_mask = pd.Series(True, index=fresh_lots.index)
            for column in lot_identity:
                current_mask &= fresh_lots[column].astype(str).eq(clean(original.get(column)))
            current_lot_quantity = int(fresh_lots.loc[current_mask, "Stock"].sum())
        if current_lot_quantity != old_quantity:
            raise core.InventoryError("Το υπόλοιπο της παρτίδας άλλαξε. Κάνε ανανέωση και ξαναδιάλεξέ την.")

    pending_deltas: dict[tuple[str, str, int], int] = {}
    for row in pending:
        key = (row["CodeType"], row["CodeValue"], int(row["LocationId"]))
        pending_deltas[key] = pending_deltas.get(key, 0) + int(row["DeltaQty"])
    for (pending_type, pending_value, pending_location), delta in pending_deltas.items():
        if core.current_stock(fresh, pending_type, pending_value, pending_location) + delta < 0:
            raise core.InventoryError("Το διαθέσιμο stock άλλαξε. Κάνε ανανέωση και ξαναδιάλεξε την παρτίδα.")

    # Update the confirmed product master after validation so a failed ledger
    # request can be retried from the same form without losing the correction.
    base_db.update_product_details_from_transaction(core, add_row)
    read_products.clear()

    if pending:
        try:
            if len(pending) > 1 and hasattr(ws, "append_rows"):
                headers, _ = core.validate_and_migrate_headers(ws)
                writable_headers = [header for header in headers if header not in core.DEPRECATED_COLUMNS]
                values = [[row.get(header, "") for header in writable_headers] for row in pending]
                ws.append_rows(values, value_input_option="RAW")
            else:
                for row in pending:
                    core.append_row(ws, row)
        except Exception:
            core.invalidate_data_cache(ws)
            raise
        core.invalidate_data_cache(ws)

    verified, _ = core.load_data(ws)
    if any(not core.transaction_exists(verified, row["TransactionId"]) for row in corrections):
        raise core.InventoryError("Η διόρθωση αποθηκεύτηκε μερικώς. Πάτησε ξανά αποθήκευση με τα ίδια στοιχεία.")

    concurrent_change = False
    for row in corrections:
        if int(row["DeltaQty"]) >= 0:
            continue
        location = int(row["LocationId"])
        balance = core.current_stock(verified, row["CodeType"], row["CodeValue"], location)
        if balance >= 0:
            continue
        concurrent_change = True
        compensation_delta = abs(balance)
        compensation_base = f"stock-edit-{edit_key}-race-{location}"
        compensation_id = compensation_base
        suffix = 1
        while core.transaction_exists(verified, compensation_id):
            suffix += 1
            compensation_id = f"{compensation_base}-{suffix}"
        compensation = core.make_transaction(
            code_type=row["CodeType"],
            code_value=row["CodeValue"],
            barcode=row["Barcode"],
            pc_code=row.get("PCCode", ""),
            gtin=row.get("GTIN", ""),
            serial_number=row.get("SerialNumber", ""),
            lot_number=row["LotNumber"],
            expiry_date=row["ExpiryDate"],
            qr_raw_data=row.get("QRRawData", ""),
            datamatrix_raw_data=row.get("DataMatrixRawData", ""),
            strength=row["Strength"],
            dosage_form=row["DosageForm"],
            brand=row["Μάρκα"],
            product=row["Προϊόν"],
            category=row["Κατηγορία"],
            location_id=location,
            movement="Αντιστάθμιση ταυτόχρονης αλλαγής (+)",
            quantity=compensation_delta,
            delta=compensation_delta,
            note=f"source=stock_edit; edit_id={edit_key}; compensates={row['TransactionId']}",
            transaction_id=compensation_id,
            void_of=row["TransactionId"],
            movement_kind=core.COMPENSATION,
        )
        core.append_stock_transaction(ws, compensation)
        verified, _ = core.load_data(ws)
        if core.current_stock(verified, row["CodeType"], row["CodeValue"], location) < 0:
            raise core.InventoryError("Το stock άλλαξε ταυτόχρονα και χρειάζεται έλεγχος πριν συνεχίσεις.")

    core.invalidate_data_cache(ws)
    if concurrent_change:
        return "race_compensated"
    return "saved" if pending else "duplicate"


def detect_barcode_from_camera(upload) -> str:
    if upload is None:
        return ""
    image = core.to_img(upload)
    if image is None:
        return ""
    detected_type, detected_value, debug = core.detect_code(None, image)
    value = clean(detected_value)
    if detected_type in {"DataMatrix", "QR"} and value:
        parsed = core.parse_machine_readable_fields(value)
        value = clean(parsed.get("gtin")) or value
    if not value:
        api_key = clean(st.secrets.get("OPENAI_API_KEY", ""))
        model = configured_openai_model()
        if api_key:
            try:
                fallback = ai_inventory.read_barcode_digits(
                    upload.getvalue(),
                    api_key=api_key,
                    model=model,
                    mime_type=clean(getattr(upload, "type", "")) or "image/jpeg",
                )
                candidate = clean(fallback.get("digits"))
                validation = core.classify_barcode_value("Barcode", candidate)
                if fallback.get("confidence") != "low" and validation.get("valid"):
                    value = candidate
                    debug["ai_digit_fallback"] = {
                        "accepted": True,
                        "confidence": fallback.get("confidence"),
                    }
                else:
                    debug["ai_digit_fallback"] = {
                        "accepted": False,
                        "confidence": fallback.get("confidence"),
                        "reason": "invalid_length_or_check_digit",
                    }
            except Exception as exc:
                debug["ai_digit_fallback"] = {"accepted": False, "error": str(exc)}
        else:
            debug["ai_digit_fallback"] = {
                "accepted": False,
                "reason": "missing_openai_api_key",
            }
    st.session_state["scan_debug"] = debug
    return value


def validated_live_barcode(raw_value: Any) -> str:
    value = clean(raw_value).replace(" ", "")
    candidate = core.classify_barcode_value("Barcode", value)
    return value if candidate.get("valid") else ""


def accept_detected_barcode(detected: str) -> bool:
    """Synchronize scanner and manual widget state, clearing stale products."""
    detected = clean(detected)
    if not detected:
        return False
    changed = detected != clean(st.session_state.get("active_barcode"))
    st.session_state["active_barcode"] = detected
    st.session_state["manual_barcode"] = detected
    if changed:
        st.session_state.pop("lookup_candidates", None)
        st.session_state.pop("selected_candidate_index", None)
    return changed


def next_live_scan_token(state: Any, detected: str) -> str:
    """Return one token per distinct live-scanner event.

    Streamlit reruns while the scanner component can still contain its previous
    value.  Treating every rerun as a scan would add stock repeatedly, so the
    same visible value stays latched until the scanner reports an empty or a
    different value.
    """
    detected = clean(detected)
    if not detected:
        state["last_live_scan_value"] = ""
        return ""
    if clean(state.get("last_live_scan_value")) == detected:
        return ""
    sequence = int(state.get("live_scan_sequence", 0)) + 1
    state["live_scan_sequence"] = sequence
    state["last_live_scan_value"] = detected
    return f"live:{sequence}:{detected}"


def auto_add_recognized_barcode(
    code: str,
    scan_token: str,
    *,
    state: Any = None,
    location_id: int = 0,
) -> dict[str, str]:
    """Add exactly one item for a known barcode, once per scan event."""
    state = st.session_state if state is None else state
    code = clean(code)
    scan_token = clean(scan_token)
    if not code or not scan_token:
        return {"status": "ignored", "product_name": ""}
    if clean(state.get("last_auto_stock_scan_token")) == scan_token:
        return {"status": "duplicate", "product_name": ""}

    product = local_product_by_code(code)
    product_name = clean((product or {}).get("product_name"))
    if not product_name:
        return {"status": "unknown", "product_name": ""}

    if not clean(state.get("auto_scan_session_id")):
        state["auto_scan_session_id"] = uuid.uuid4().hex
    transaction_id = "auto-scan-" + hashlib.sha256(
        f"{state['auto_scan_session_id']}|{scan_token}".encode("utf-8")
    ).hexdigest()[:24]

    try:
        save_inventory_item(
            code=code,
            product_name=product_name,
            quantity=1,
            brand=clean(product.get("brand")),
            strength=clean(product.get("strength")),
            dosage_form=clean(product.get("dosage_form")),
            expiry_date="",
            lot_number="",
            category=clean(product.get("category")) or DEFAULT_CATEGORY,
            location_id=location_id,
            transaction_id=transaction_id,
            note=(
                f"source=automatic_barcode_scan; scan_token={scan_token}; "
                "quantity=1; expiry_not_captured=true"
            ),
        )
    except Exception as exc:
        return {"status": "error", "product_name": product_name, "error": str(exc)}

    state["last_auto_stock_scan_token"] = scan_token
    return {"status": "added", "product_name": product_name}


def show_auto_add_result(result: dict[str, str]) -> None:
    status = clean(result.get("status"))
    if status == "added":
        st.success(f"✅ Προστέθηκε αμέσως +1: {clean(result.get('product_name'))}")
        st.caption("Η αυτόματη σάρωση προσθέτει στην Αποθήκη χωρίς ημερομηνία λήξης.")
    elif status == "error":
        st.error(f"Το barcode διαβάστηκε, αλλά το +1 δεν αποθηκεύτηκε: {clean(result.get('error'))}")


def _clear_scan_state() -> None:
    for key in [
        "active_barcode",
        "lookup_candidates",
        "selected_candidate_index",
        "scan_debug",
        "manual_barcode",
        "last_camera_hash",
        "barcode_camera",
        "product_name_reference_camera",
        "last_live_scan_value",
        "last_auto_stock_scan_token",
    ]:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if key.startswith(("product_name_", "brand_", "strength_", "form_", "package_", "expiry_", "no_expiry_", "lot_", "confirm_", "quantity_", "location_")):
            st.session_state.pop(key, None)


def resolve_barcode(code: str, force_online: bool = False) -> list[dict[str, Any]]:
    code = clean(code)
    if not code:
        return []
    if not force_online:
        local = local_product_by_code(code)
        if local:
            return [local]
    return lookup_barcode_online(code)


def render_candidate_picker(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        st.warning("Δεν βρήκα ασφαλή αντιστοίχιση online. Γράψε το όνομα χειροκίνητα και επιβεβαίωσέ το.")
        return {
            "product_name": "",
            "brand": "",
            "strength": "",
            "dosage_form": "",
            "package_size": "",
            "category": DEFAULT_CATEGORY,
            "source": "Χειροκίνητη καταχώρηση",
            "url": "",
            "confidence": 0.0,
        }

    labels = []
    for idx, candidate in enumerate(candidates):
        title = clean(candidate.get("product_name")) or "Χωρίς σαφές όνομα"
        source = clean(candidate.get("source")) or "internet"
        labels.append(f"{idx + 1}. {title} · {source}")
    choice = st.radio(
        "Πιθανό προϊόν",
        options=list(range(len(labels))),
        format_func=lambda idx: labels[idx],
        key="selected_candidate_index",
    )
    selected = candidates[int(choice)]
    if clean(selected.get("url")):
        st.caption(f"Πηγή: {clean(selected.get('source'))} · {clean(selected.get('url'))}")
    else:
        st.caption(f"Πηγή: {clean(selected.get('source'))}")
    return selected


def scan_tab() -> None:
    if st.session_state.pop("_scan_reset_pending", False):
        _clear_scan_state()

    st.subheader("📷 Σκανάρισμα barcode")
    st.caption("Σκανάρεις → βρίσκω όνομα → εσύ λες OK → βάζεις ποσότητα → αποθήκευση.")
    st.caption(f"Έκδοση εφαρμογής: {APP_VERSION}")

    if st.button("🧹 Καθαρισμός προηγούμενης σάρωσης", width="stretch"):
        _clear_scan_state()
        st.rerun()

    scan_method = st.segmented_control(
        "Τρόπος σάρωσης",
        ["Ζωντανός scanner", "Φωτογραφία"],
        default="Ζωντανός scanner",
        key="barcode_scan_method",
    )

    if scan_method == "Ζωντανός scanner":
        st.caption("Στόχευσε τις γραμμές του barcode. Η κάμερα το διαβάζει ζωντανά χωρίς API.")
        if qrcode_scanner is None:
            st.error("Ο ζωντανός scanner δεν εγκαταστάθηκε. Επίλεξε Φωτογραφία.")
        else:
            live_value = qrcode_scanner(key="live_barcode_scanner")
            detected = validated_live_barcode(live_value) if live_value else ""
            scan_token = next_live_scan_token(st.session_state, detected)
            if detected:
                accept_detected_barcode(detected)
                if scan_token:
                    result = auto_add_recognized_barcode(detected, scan_token)
                    show_auto_add_result(result)
                    if result["status"] == "unknown":
                        st.info("Το barcode είναι νέο. Κάνε αντιστοίχιση προϊόντος μία φορά στην παρακάτω φόρμα.")
                else:
                    st.success(f"Διαβάστηκε: {detected}")
            elif live_value:
                st.warning("Διαβάστηκε κωδικός αλλά απέτυχε ο έλεγχος εγκυρότητας. Ξαναστόχευσε.")
    else:
        camera = st.camera_input(
            "Φωτογράφισε το barcode",
            key="barcode_camera",
            help="Κράτα το barcode καθαρό και σχετικά κοντά στην κάμερα.",
        )
        if camera is not None:
            camera_hash = hashlib.sha256(camera.getvalue()).hexdigest()
            if st.session_state.get("last_camera_hash") != camera_hash:
                with st.spinner("Διαβάζω barcode..."):
                    detected = detect_barcode_from_camera(camera)
                st.session_state["last_camera_hash"] = camera_hash
                if detected:
                    accept_detected_barcode(detected)
                    result = auto_add_recognized_barcode(detected, f"photo:{camera_hash}")
                    show_auto_add_result(result)
                    if result["status"] == "unknown":
                        st.info("Το barcode είναι νέο. Κάνε αντιστοίχιση προϊόντος μία φορά στην παρακάτω φόρμα.")
                else:
                    fallback = st.session_state.get("scan_debug", {}).get("ai_digit_fallback", {})
                    if fallback.get("reason") == "missing_openai_api_key":
                        st.error("Δεν διαβάστηκε barcode και λείπει το OPENAI_API_KEY από τα Streamlit Secrets.")
                    elif fallback.get("error"):
                        st.error("Δεν διαβάστηκε barcode και απέτυχε η εφεδρική ανάγνωση εικόνας. Έλεγξε το API key/model στα Secrets.")
                    else:
                        st.error("Δεν διαβάστηκε barcode από τη φωτογραφία. Γράψ' το χειροκίνητα.")

    manual_code = st.text_input(
        "ή γράψε το barcode",
        value=clean(st.session_state.get("active_barcode")),
        placeholder="π.χ. 5201234567890",
        key="manual_barcode",
    )
    code = clean(manual_code) or clean(st.session_state.get("active_barcode"))
    if code and code != clean(st.session_state.get("active_barcode")):
        st.session_state["active_barcode"] = code
        st.session_state.pop("lookup_candidates", None)

    with st.expander("🛠️ Διαγνωστικά barcode", expanded=False):
        st.write({
            "app_version": APP_VERSION,
            "scanner_barcode": clean(st.session_state.get("active_barcode")),
            "manual_barcode": clean(manual_code),
            "effective_barcode": code,
            "catalog_product": (
                (lookup_pharmacy_product(code) or {}).get("product_name")
                or lookup_starter_product(code)
            ),
        })
        st.caption("Το τεστ ελέγχει τις πηγές από τον server του Streamlit με 3 πραγματικά barcode.")
        if st.button("🧪 Τεστ κάλυψης e-shops", key="provider_benchmark_button"):
            with st.spinner("Ελέγχω τις πηγές — μπορεί να χρειαστεί έως ένα λεπτό..."):
                st.session_state["provider_benchmark"] = core.benchmark_provider_coverage(PROVIDER_BENCHMARK_CODES)
        if st.session_state.get("provider_benchmark"):
            st.dataframe(st.session_state["provider_benchmark"], hide_index=True, width="stretch")

    c1, c2 = st.columns(2)
    search_clicked = c1.button("🔎 Βρες προϊόν", type="primary", width="stretch", disabled=not code)
    online_clicked = c2.button("🌐 Ψάξε ξανά online", width="stretch", disabled=not code)
    if search_clicked or online_clicked:
        with st.spinner("Ψάχνω πρώτα τη δική σου βάση και μετά online..."):
            st.session_state["lookup_candidates"] = resolve_barcode(code, force_online=online_clicked)

    with st.expander("📸 Φωτογραφία ονόματος", expanded=False):
        st.caption("Μόνο για να τη βλέπεις εσύ. Δεν γίνεται OCR και δεν προτείνεται όνομα από τη φωτογραφία.")
        name_photo = st.camera_input(
            "Φωτογράφισε το όνομα του προϊόντος",
            key="product_name_reference_camera",
            help="Η εικόνα είναι μόνο οπτική αναφορά για σένα.",
        )
        if name_photo is not None:
            st.image(name_photo, caption="Οπτική αναφορά ονόματος", width=320)

    candidates = st.session_state.get("lookup_candidates")
    if candidates is None:
        return

    selected = render_candidate_picker(candidates)
    with st.expander("🛠️ Διαγνωστικά αποτελέσματος", expanded=False):
        st.write({
            "effective_barcode": code,
            "product_name": clean(selected.get("product_name")),
            "source": clean(selected.get("source")),
            "confidence": selected.get("confidence", ""),
        })
    context = hashlib.sha256((code + clean(selected.get("product_name"))).encode()).hexdigest()[:10]
    product_name = st.text_input(
        "Όνομα προϊόντος",
        value=clean(selected.get("product_name")),
        key=f"product_name_{context}",
        help="Διόρθωσέ το χειροκίνητα αν χρειάζεται.",
    )
    brand = st.text_input("Μάρκα / εταιρεία", value=clean(selected.get("brand")), key=f"brand_{context}")
    c3, c4, c5 = st.columns(3)
    strength = c3.text_input("Περιεκτικότητα", value=clean(selected.get("strength")), key=f"strength_{context}")
    dosage_form = c4.text_input("Μορφή", value=clean(selected.get("dosage_form")), key=f"form_{context}")
    package_size = c5.text_input(
        "Μέγεθος συσκευασίας",
        value=clean(selected.get("package_size")),
        key=f"package_{context}",
        help="Π.χ. 60 CAPS ή 100 ML. Δεν είναι η ποσότητα stock.",
    )

    confirmed = st.checkbox(
        f"OK, το barcode {code} αντιστοιχεί σε αυτό το προϊόν",
        key=f"confirm_{context}",
    )
    if not confirmed:
        st.info("Δεν αποθηκεύεται τίποτα πριν το OK.")
        return

    quantity = st.number_input("Ποσότητα", min_value=1, value=1, step=1, key=f"quantity_{context}")
    expiry_date = st.date_input(
        "Ημερομηνία λήξης",
        value=None,
        format="DD/MM/YYYY",
        key=f"expiry_{context}",
        help="Θα εμφανιστεί προειδοποίηση όταν απομένουν 6 μήνες ή λιγότερο.",
    )
    no_expiry = st.checkbox("Δεν υπάρχει ημερομηνία λήξης", key=f"no_expiry_{context}")
    lot_number = st.text_input("Παρτίδα (προαιρετικό)", key=f"lot_{context}")
    location_label = st.selectbox(
        "Τοποθεσία",
        [f"{idx} - {name}" for idx, name in LOCATIONS.items()],
        key=f"location_{context}",
    )
    location_id = int(location_label.split("-", 1)[0].strip())

    if st.button("💾 Αποθήκευση", type="primary", width="stretch", key=f"save_{context}"):
        try:
            if expiry_date is None and not no_expiry:
                raise ValueError("Συμπλήρωσε ημερομηνία λήξης ή επίλεξε ότι δεν υπάρχει.")
            save_inventory_item(
                code=code,
                product_name=product_name,
                quantity=int(quantity),
                brand=brand,
                strength=strength,
                dosage_form=dosage_form,
                expiry_date=expiry_date.isoformat() if expiry_date is not None else "",
                lot_number=lot_number,
                category=clean(selected.get("category")) or DEFAULT_CATEGORY,
                location_id=location_id,
                note=(
                    f"source={clean(selected.get('source')) or 'manual'}; "
                    f"package_size={clean(package_size)}; verified_by_user=true"
                ),
            )
            st.success(f"Αποθηκεύτηκαν {int(quantity)} τεμάχια: {product_name}")
            st.session_state["_scan_reset_pending"] = True
            st.rerun()
        except Exception as exc:
            st.error(f"Δεν αποθηκεύτηκε: {exc}")


def _invoice_dataframe_from_upload(file) -> pd.DataFrame:
    name = file.name.lower()
    raw = file.getvalue()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw))
    if name.endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(raw))
    raise ValueError("Υποστηρίζονται CSV και XLSX.")


def _normalize_invoice_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["ProductName", "Quantity", "Barcode", "ExpiryDate", "NoExpiry", "OK"])
    lower = {str(col).strip().lower(): col for col in df.columns}
    name_col = next((lower[k] for k in lower if any(token in k for token in ["product", "προϊόν", "description", "περιγραφή", "item"])), None)
    qty_col = next((lower[k] for k in lower if any(token in k for token in ["quantity", "qty", "ποσότητα", "τεμάχ"])), None)
    if name_col is None:
        name_col = df.columns[0]
    if qty_col is None and len(df.columns) > 1:
        qty_col = df.columns[1]
    out = pd.DataFrame()
    out["ProductName"] = df[name_col].map(clean)
    out["Quantity"] = pd.to_numeric(df[qty_col], errors="coerce").fillna(1).astype(int) if qty_col is not None else 1
    out["Barcode"] = ""
    out["ExpiryDate"] = ""
    out["NoExpiry"] = False
    out["OK"] = False
    return out[out["ProductName"].ne("") & out["Quantity"].gt(0)].reset_index(drop=True)


def _invoice_rows_from_ai(images: list[dict[str, Any]], api_key: str, model: str) -> pd.DataFrame:
    result = ai_inventory.analyze_images(
        images,
        api_key=api_key,
        mode="Τιμολόγιο / παραλαβή",
        default_vat=24.0,
        model=model,
    )
    rows = []
    for item in result.get("items", []):
        product = clean(item.get("ProductName"))
        qty_value = pd.to_numeric(item.get("Quantity"), errors="coerce")
        qty = 1 if pd.isna(qty_value) else int(qty_value)
        if product and qty > 0:
            rows.append({"ProductName": product, "Quantity": qty, "Barcode": "", "ExpiryDate": "", "NoExpiry": False, "OK": False})
    return pd.DataFrame(rows, columns=["ProductName", "Quantity", "Barcode", "ExpiryDate", "NoExpiry", "OK"])


def invoice_tab() -> None:
    st.subheader("🧾 Τιμολόγια")
    st.caption("Κρατάμε όνομα + ποσότητα. Το barcode επιβεβαιώνεται από εσένα πριν περάσει στο stock.")

    method = st.segmented_control(
        "Πηγή",
        ["Φωτογραφία τιμολογίου", "CSV / XLSX", "Χειροκίνητα"],
        default="Φωτογραφία τιμολογίου",
        key="invoice_method",
    )

    if method == "Φωτογραφία τιμολογίου":
        api_key = clean(st.secrets.get("OPENAI_API_KEY", ""))
        model = configured_openai_model()
        uploads = st.file_uploader(
            "Φωτογραφίες τιμολογίου",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="invoice_images",
        )
        if st.button("📄 Διάβασε όνομα + ποσότητα", type="primary", disabled=not uploads, width="stretch"):
            if not api_key:
                st.error("Λείπει OPENAI_API_KEY από τα Streamlit Secrets.")
            else:
                images = [{"bytes": f.getvalue(), "name": f.name, "type": getattr(f, "type", "image/jpeg")} for f in uploads]
                try:
                    with st.spinner("Διαβάζω προϊόντα και ποσότητες..."):
                        st.session_state["invoice_rows"] = _invoice_rows_from_ai(images, api_key, model)
                except Exception as exc:
                    st.error(f"Δεν διαβάστηκε το τιμολόγιο: {exc}")

    elif method == "CSV / XLSX":
        upload = st.file_uploader("Αρχείο τιμολογίου", type=["csv", "xlsx"], key="invoice_file")
        if st.button("📄 Φόρτωσε γραμμές", type="primary", disabled=upload is None, width="stretch"):
            try:
                st.session_state["invoice_rows"] = _normalize_invoice_rows(_invoice_dataframe_from_upload(upload))
            except Exception as exc:
                st.error(str(exc))

    else:
        c1, c2 = st.columns([3, 1])
        name = c1.text_input("Όνομα προϊόντος", key="invoice_manual_name")
        qty = c2.number_input("Ποσότητα", min_value=1, value=1, step=1, key="invoice_manual_qty")
        if st.button("➕ Πρόσθεσε γραμμή", disabled=not clean(name)):
            rows = st.session_state.get("invoice_rows")
            if not isinstance(rows, pd.DataFrame):
                rows = pd.DataFrame(columns=["ProductName", "Quantity", "Barcode", "ExpiryDate", "NoExpiry", "OK"])
            st.session_state["invoice_rows"] = pd.concat(
                [rows, pd.DataFrame([{"ProductName": clean(name), "Quantity": int(qty), "Barcode": "", "ExpiryDate": "", "NoExpiry": False, "OK": False}])],
                ignore_index=True,
            )

    rows = st.session_state.get("invoice_rows")
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return
    for column, default in [("ExpiryDate", ""), ("NoExpiry", False), ("OK", False)]:
        if column not in rows.columns:
            rows[column] = default

    st.markdown("**Βάλε barcode σε κάθε γραμμή και τσέκαρε OK.**")
    edited = st.data_editor(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "ProductName": st.column_config.TextColumn("Προϊόν"),
            "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=1, step=1),
            "Barcode": st.column_config.TextColumn("Barcode"),
            "ExpiryDate": st.column_config.TextColumn("Λήξη", help="MM/YYYY ή DD/MM/YYYY"),
            "NoExpiry": st.column_config.CheckboxColumn("Χωρίς λήξη"),
            "OK": st.column_config.CheckboxColumn("OK"),
        },
        key="invoice_editor",
    )
    st.session_state["invoice_rows"] = edited

    chosen = edited[edited["OK"] == True].copy()
    invalid = chosen[chosen["Barcode"].astype(str).str.strip().eq("")]
    if not invalid.empty:
        st.warning("Υπάρχουν επιβεβαιωμένες γραμμές χωρίς barcode. Αυτές δεν θα περάσουν.")
    missing_expiry = chosen[
        chosen["ExpiryDate"].astype(str).str.strip().eq("")
        & ~chosen["NoExpiry"].fillna(False).astype(bool)
    ]
    if not missing_expiry.empty:
        st.warning("Υπάρχουν επιβεβαιωμένες γραμμές χωρίς λήξη. Συμπλήρωσέ την ή τσέκαρε «Χωρίς λήξη».")

    location_label = st.selectbox("Παραλαβή σε", [f"{idx} - {name}" for idx, name in LOCATIONS.items()], key="invoice_location")
    location_id = int(location_label.split("-", 1)[0].strip())
    valid = chosen[
        chosen["Barcode"].astype(str).str.strip().ne("")
        & (chosen["ExpiryDate"].astype(str).str.strip().ne("") | chosen["NoExpiry"].fillna(False).astype(bool))
    ]

    if st.button(
        f"💾 Πέρασε {len(valid)} επιβεβαιωμένες γραμμές στο stock",
        type="primary",
        disabled=valid.empty,
        width="stretch",
    ):
        saved = 0
        errors = []
        for _, row in valid.iterrows():
            try:
                normalized_expiry = core.parse_expiry_date(clean(row["ExpiryDate"])) if clean(row["ExpiryDate"]) else ""
                save_inventory_item(
                    code=clean(row["Barcode"]),
                    product_name=clean(row["ProductName"]),
                    quantity=int(row["Quantity"]),
                    location_id=location_id,
                    expiry_date=normalized_expiry,
                    note="source=invoice; verified_by_user=true",
                )
                saved += 1
            except Exception as exc:
                errors.append(f"{clean(row['ProductName'])}: {exc}")
        if errors:
            st.error(" | ".join(errors[:5]))
        if saved:
            st.success(f"Πέρασαν {saved} γραμμές στο stock.")
            st.session_state.pop("invoice_rows", None)
            st.rerun()


def stock_tab() -> None:
    st.subheader("📦 Τρέχον stock")
    ws = core.worksheet()
    try:
        data, _ = core.load_data_cached(ws, ttl_seconds=STOCK_CACHE_TTL_SECONDS)
    except Exception as exc:
        if "429" in str(exc) or "quota" in str(exc).lower():
            st.warning("Το Google Sheets έπιασε προσωρινά το όριο αναγνώσεων. Περίμενε λίγο και ξαναδοκίμασε.")
            return
        raise
    stock = core.stock_table(data)
    if stock.empty:
        st.info("Δεν υπάρχουν ακόμα κινήσεις stock.")
        return
    snapshot = csa.stock_snapshot(data)
    if not snapshot.empty:
        expiry_view = core.add_expiry_columns(snapshot)
        expired_count = int(expiry_view["ExpiryStatus"].eq("expired").sum())
        six_month_count = int(expiry_view["ExpiryStatus"].eq("expiring_soon").sum())
        c1, c2 = st.columns(2)
        c1.metric("Ληγμένα", expired_count)
        c2.metric("Λήγουν μέσα σε 6 μήνες", six_month_count)
        alerts = expiry_view[expiry_view["ExpiryStatus"].isin(["expired", "expiring_soon"])]
        if not alerts.empty:
            st.warning("Υπάρχουν προϊόντα που έχουν λήξει ή πλησιάζουν το όριο των 6 μηνών.")
            alert_columns = ["Προϊόν", "Barcode", "GTIN", "LotNumber", "ExpiryDate", "ExpiryWarning", "Stock", "Τοποθεσία"]
            st.dataframe(alerts[[column for column in alert_columns if column in alerts]], hide_index=True, width="stretch")
    query = st.text_input("Αναζήτηση", placeholder="όνομα, barcode, μάρκα...")
    if query:
        stock, _ = core.search_stock(stock, query)
    columns = ["Προϊόν", "Μάρκα", "Barcode", "GTIN", "ExpiryDate", "ExpiryWarning", "Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος", "Σύνολο"]
    available = [column for column in columns if column in stock.columns]
    st.dataframe(stock[available], hide_index=True, width="stretch")

    editable_lots = snapshot[snapshot["LocationId"].isin(LOCATIONS)].copy()
    if editable_lots.empty:
        return
    with st.expander("✏️ Επεξεργασία αποθηκευμένου προϊόντος", expanded=False):
        st.caption(
            "Διάλεξε συγκεκριμένη παρτίδα για να διορθώσεις στοιχεία, λήξη, ποσότητα ή θέση. "
            "Το barcode παραμένει ίδιο. Οι παλιές κινήσεις διατηρούνται στο ιστορικό."
        )
        editable_lots = editable_lots.reset_index(drop=True)
        option_labels = []
        for _, item in editable_lots.iterrows():
            code = clean(item.get("Barcode")) or clean(item.get("GTIN")) or clean(item.get("CodeValue"))
            lot = clean(item.get("LotNumber")) or "χωρίς παρτίδα"
            expiry = clean(item.get("ExpiryDate")) or "χωρίς λήξη"
            place = LOCATIONS.get(int(item.get("LocationId", 0)), "Άγνωστη θέση")
            option_labels.append(
                f"{clean(item.get('Προϊόν'))} · {code} · LOT {lot} · {expiry} · {place} · {int(item.get('Stock', 0))} τεμ."
            )
        selected_index = st.selectbox(
            "Ποια εγγραφή θέλεις να αλλάξεις;",
            options=range(len(option_labels)),
            format_func=lambda index: option_labels[index],
            key="stock_edit_selection",
        )
        original = editable_lots.iloc[int(selected_index)]
        context = hashlib.sha256(
            "|".join(
                clean(original.get(column))
                for column in [
                    "CodeType", "CodeValue", "Barcode", "PCCode", "GTIN", "SerialNumber",
                    "LotNumber", "ExpiryDate", "QRRawData", "DataMatrixRawData", "Strength",
                    "DosageForm", "Μάρκα", "Προϊόν", "Κατηγορία", "LocationId", "Stock",
                ]
            ).encode("utf-8")
        ).hexdigest()[:12]
        st.caption(f"Barcode / κωδικός: {clean(original.get('CodeValue'))}")

        current_expiry_value = pd.to_datetime(clean(original.get("ExpiryDate")), errors="coerce")
        current_expiry = None if pd.isna(current_expiry_value) else current_expiry_value.date()
        categories = list(core.CATEGORIES)
        current_category = clean(original.get("Κατηγορία")) or DEFAULT_CATEGORY
        if current_category not in categories:
            categories.append(current_category)
        locations = [f"{index} - {name}" for index, name in LOCATIONS.items()]
        current_location = int(original.get("LocationId", 0))
        current_location_index = next(
            (index for index, label in enumerate(locations) if int(label.split("-", 1)[0].strip()) == current_location),
            0,
        )

        pending_key = f"stock_edit_pending_{context}"
        state_key = f"stock_edit_form_{context}"
        if state_key not in st.session_state:
            st.session_state[state_key] = uuid.uuid4().hex
        with st.form(f"stock_edit_form_{context}"):
            edited_name = st.text_input("Όνομα προϊόντος", value=clean(original.get("Προϊόν")), key=f"stock_edit_name_{context}")
            edited_brand = st.text_input("Μάρκα / εταιρεία", value=clean(original.get("Μάρκα")), key=f"stock_edit_brand_{context}")
            col_strength, col_form = st.columns(2)
            edited_strength = col_strength.text_input("Περιεκτικότητα", value=clean(original.get("Strength")), key=f"stock_edit_strength_{context}")
            edited_form = col_form.text_input("Μορφή", value=clean(original.get("DosageForm")), key=f"stock_edit_dosage_{context}")
            edited_category = st.selectbox(
                "Κατηγορία",
                categories,
                index=categories.index(current_category),
                key=f"stock_edit_category_{context}",
            )
            col_quantity, col_lot = st.columns(2)
            edited_quantity = col_quantity.number_input(
                "Σωστή ποσότητα",
                min_value=0,
                max_value=1 if clean(original.get("SerialNumber")) and int(original.get("Stock", 0)) <= 1 else None,
                value=int(original.get("Stock", 0)),
                step=1,
                key=f"stock_edit_quantity_{context}",
            )
            edited_lot = col_lot.text_input("Παρτίδα", value=clean(original.get("LotNumber")), key=f"stock_edit_lot_{context}")
            has_expiry = st.checkbox(
                "Υπάρχει ημερομηνία λήξης",
                value=current_expiry is not None,
                key=f"stock_edit_has_expiry_{context}",
            )
            edited_expiry = None
            if has_expiry:
                edited_expiry = st.date_input(
                    "Ημερομηνία λήξης",
                    value=current_expiry,
                    format="DD/MM/YYYY",
                    key=f"stock_edit_expiry_{context}",
                )
            edited_location_label = st.selectbox(
                "Τοποθεσία",
                locations,
                index=current_location_index,
                key=f"stock_edit_location_{context}",
            )
            edit_reason = st.text_input("Αιτία αλλαγής (προαιρετικό)", key=f"stock_edit_reason_{context}")
            confirmed = st.checkbox("Επιβεβαιώνω τη διόρθωση", key=f"stock_edit_confirm_{context}")
            submitted = st.form_submit_button("💾 Αποθήκευση αλλαγών", type="primary", disabled=not confirmed, width="stretch")

        if submitted:
            if has_expiry and edited_expiry is None:
                st.error("Διάλεξε ημερομηνία λήξης ή βγάλε την επιλογή «Υπάρχει ημερομηνία λήξης».")
                return
            location_id = int(edited_location_label.split("-", 1)[0].strip())
            payload = (
                clean(edited_name), clean(edited_brand), clean(edited_strength), clean(edited_form),
                clean(edited_category), int(edited_quantity),
                edited_expiry.isoformat() if has_expiry and edited_expiry else "",
                clean(edited_lot), location_id, clean(edit_reason),
            )
            pending = st.session_state.get(pending_key)
            if pending and pending.get("payload") != payload:
                st.error("Μια διόρθωση εκκρεμεί. Κάνε ξανά αποθήκευση με τα ίδια στοιχεία πριν τα αλλάξεις.")
                return
            if not pending:
                pending = {"payload": payload, "edit_id": uuid.uuid4().hex}
                st.session_state[pending_key] = pending
            try:
                result = edit_stock_lot(
                    ws,
                    original,
                    product_name=payload[0],
                    brand=payload[1],
                    strength=payload[2],
                    dosage_form=payload[3],
                    category=payload[4],
                    quantity=payload[5],
                    expiry_date=payload[6],
                    lot_number=payload[7],
                    location_id=payload[8],
                    reason=payload[9],
                    edit_id=pending["edit_id"],
                )
            except (ValueError, core.InventoryError) as exc:
                message = str(exc)
                if isinstance(exc, ValueError) or message.startswith((
                    "Η ημερομηνία λήξης", "Το υπόλοιπο της παρτίδας άλλαξε", "Το διαθέσιμο stock άλλαξε",
                )):
                    st.session_state.pop(pending_key, None)
                st.error(f"Δεν ολοκληρώθηκε η διόρθωση: {message}")
            except Exception as exc:
                st.error(f"Δεν ολοκληρώθηκε η διόρθωση: {exc}")
            else:
                st.session_state.pop(pending_key, None)
                if result == "race_compensated":
                    st.warning("Άλλαξε κίνηση stock την ίδια στιγμή. Αποτράπηκε αρνητικό υπόλοιπο· έλεγξε ξανά την ποσότητα μετά την ανανέωση.")
                elif result == "duplicate":
                    st.info("Αυτή η διόρθωση είχε ήδη αποθηκευτεί.")
                else:
                    st.success("Οι αλλαγές αποθηκεύτηκαν. Το ιστορικό των προηγούμενων κινήσεων διατηρήθηκε.")
                st.rerun()


def catalog_dataframe() -> pd.DataFrame:
    rows: dict[str, dict[str, str]] = {}
    pharmacy_catalog = pharmacy_catalog_dataframe()
    for _, item in pharmacy_catalog.iterrows():
        product_name = clean(item.get("ProductName"))
        barcodes = clean(item.get("Barcodes")).replace("|", " | ")
        verified_names = {
            lookup_starter_product(value.strip())
            for value in barcodes.split("|")
            if lookup_starter_product(value.strip())
        }
        if verified_names:
            product_name = sorted(verified_names)[0]
        attributes = core.extract_commercial_attributes(product_name)
        brand = next(
            (candidate for candidate in ["AVENE", "LIERAC", "LAMBERTS", "SOLGAR", "FREZYDERM", "KORRES", "VICHY", "LA ROCHE-POSAY", "BIODERMA", "FROIKA", "MUSTELA", "APIVITA", "EUCERIN"] if candidate in product_name.upper()),
            "",
        )
        rows[f"catalog:{product_name.casefold()}"] = {
            "Barcode": barcodes,
            "Προϊόν": product_name,
            "Μάρκα": brand,
            "Περιεκτικότητα": attributes["strength"],
            "Μορφή": attributes["dosage_form"],
            "Συσκευασία": attributes["package_size"],
            "Κατηγορία": DEFAULT_CATEGORY,
            "Πηγή": "Κατάλογος φαρμακείου" if verified_names else "Βάση προϊόντων φαρμακείου",
        }
    for barcode, product_name in LAMBERTS_PRODUCTS.items():
        if lookup_pharmacy_product(barcode):
            continue
        attributes = core.extract_commercial_attributes(product_name)
        rows[f"starter:{barcode}"] = {
            "Barcode": barcode,
            "Προϊόν": product_name,
            "Μάρκα": "LAMBERTS",
            "Περιεκτικότητα": attributes["strength"],
            "Μορφή": attributes["dosage_form"],
            "Συσκευασία": attributes["package_size"],
            "Κατηγορία": "Συμπλήρωμα διατροφής",
            "Πηγή": "Κατάλογος φαρμακείου",
        }
    products = read_products()
    if not products.empty:
        for _, item in products.iterrows():
            barcode = clean(item.get("Barcode")) or clean(item.get("GTIN"))
            if not barcode:
                continue
            rows[f"confirmed:{barcode}"] = {
                "Barcode": barcode,
                "Προϊόν": clean(item.get("ProductName")),
                "Μάρκα": clean(item.get("Brand")),
                "Περιεκτικότητα": clean(item.get("Strength")),
                "Μορφή": clean(item.get("DosageForm")),
                "Συσκευασία": "",
                "Κατηγορία": clean(item.get("Category")),
                "Πηγή": "Δική σου βάση",
            }
    return pd.DataFrame(rows.values()).sort_values(["Μάρκα", "Προϊόν", "Barcode"], ignore_index=True)


def catalog_tab() -> None:
    st.subheader("📚 Κατάλογος προϊόντων")
    st.caption("Γνωστά προϊόντα και barcode. Η εμφάνιση εδώ δεν σημαίνει ότι υπάρχει ποσότητα στο stock.")
    catalog = catalog_dataframe()
    query = st.text_input("Αναζήτηση καταλόγου", placeholder="όνομα, barcode, μάρκα...")
    if query:
        needle = clean(query).casefold()
        mask = catalog.astype(str).apply(lambda column: column.str.casefold().str.contains(needle, regex=False)).any(axis=1)
        catalog = catalog[mask]
    st.metric("Γνωστά προϊόντα", len(catalog))
    st.dataframe(catalog, hide_index=True, width="stretch")


def main() -> None:
    st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="💊", layout="wide")
    st.title("💊 Αποθήκη Φαρμακείου")
    st.caption("Barcode πρώτα. Επιβεβαίωση από άνθρωπο μετά. Έτσι αποφεύγουμε να κάνουμε το internet υπεύθυνο φαρμακείου.")

    try:
        ensure_storage_once()
    except Exception as exc:
        text = str(exc)
        if "429" in text or "quota" in text.lower():
            st.error("Το Google Sheets έπιασε προσωρινά το όριο αναγνώσεων. Περίμενε περίπου 1 λεπτό και κάνε refresh.")
        else:
            st.error(f"Δεν συνδέθηκε το Google Sheet: {exc}")
        st.stop()

    tab_scan, tab_invoice, tab_catalog, tab_stock = st.tabs(["📷 Barcode", "🧾 Τιμολόγια", "📚 Κατάλογος", "📦 Stock"])
    with tab_scan:
        scan_tab()
    with tab_invoice:
        invoice_tab()
    with tab_catalog:
        catalog_tab()
    with tab_stock:
        stock_tab()


if __name__ == "__main__":
    main()
