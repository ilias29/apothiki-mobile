import base64
import calendar
import io
import re
from datetime import date, datetime
import uuid

import streamlit as st
import pandas as pd
from PIL import Image

import app_inventory_search as core
import photo_suggestions as photo_ai
import inventory_base as base_db
import shelf_photo as shelf_ai

CATEGORIES = ["Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Ορθοπεδικό", "Βρεφικό", "Άλλο", "Φάρμακο"]
LOCATIONS = {0: "Αποθήκη", 1: "Κάτω / Κύριο Κτήριο", 2: "Πάνω / Επίπεδο 1"}
TRUE_VALUES = {"true", "1", "yes", "y", "ναι", "nai"}
INITIAL_CHOICES = ["Όλα", "0-9", *list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), *list("ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ")]
MAX_PHOTO_CELL_CHARS = 45000
SHELF_DRAFT_COLUMNS = ["confirm", "ProductName", "EstimatedQty", "BarcodeOrGTIN", "ExpiryDate", "LotNumber", "Strength", "Category", "Confidence", "SourcePhoto", "Notes"]


def clean(x):
    return str(x or "").strip()


def up(x):
    return clean(x).upper()


def load_data():
    ws = core.worksheet()
    data, _ = core.load_data_cached(ws)
    return data.copy(deep=True)


def stock_by_code(data, code):
    if data.empty or not clean(code):
        return data.iloc[0:0]
    code = clean(code)
    for col in core.COLUMNS:
        if col not in data.columns:
            data[col] = ""
    mask = data["Barcode"].astype(str).str.strip().eq(code) | data["GTIN"].astype(str).str.strip().eq(code)
    return data[mask].copy()


def product_defaults(rows):
    if rows.empty:
        return {"product": "", "brand": "", "category": CATEGORIES[0], "strength": "", "form": ""}
    r = rows.iloc[-1]
    return {
        "product": clean(r.get("Προϊόν", "")),
        "brand": clean(r.get("Μάρκα", "")),
        "category": clean(r.get("Κατηγορία", "")) or CATEGORIES[0],
        "strength": clean(r.get("Strength", "")),
        "form": clean(r.get("DosageForm", "")),
    }


def parse_expiry(value):
    text = clean(value)
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})[./-](\d{4})", text)
    if match:
        month, year = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, last_day).isoformat()
    return text


def encode_uploaded_photo(uploaded_file, photo_kind):
    if not uploaded_file:
        return "", ""
    data = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((640, 640))
    for quality in (75, 65, 55, 45):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        payload = base64.b64encode(buffer.getvalue()).decode("ascii")
        data_url = f"data:image/jpeg;base64,{payload}"
        if len(data_url) <= MAX_PHOTO_CELL_CHARS:
            note = f"{photo_kind}_photo_saved=true; {photo_kind}_photo_chars={len(data_url)}; {photo_kind}_photo_quality={quality}"
            return data_url, note
    raise core.InventoryError(f"Η φωτογραφία {photo_kind} είναι πολύ μεγάλη για το Google Sheet. Βάλε πιο κοντινή/κομμένη φωτογραφία.")


def scan_code_from_photo(uploaded_file):
    if not uploaded_file:
        return {"code": "", "raw": "", "type": "", "gtin": "", "debug": {}}
    try:
        image = core.to_img(uploaded_file)
        if image is None:
            raise core.InventoryError("Δεν μπόρεσα να διαβάσω τη φωτογραφία QR / barcode.")
        detected_type, raw_value, debug = core.detect_code(back=image)
    except Exception as exc:
        return {"code": "", "raw": "", "type": "", "gtin": "", "debug": {"error": str(exc)}}
    selected = debug.get("selected", {}) if isinstance(debug, dict) else {}
    gtin = clean(selected.get("gtin", ""))
    raw = clean(raw_value)
    numeric_code = gtin or (raw if raw.isdigit() else "")
    return {"code": numeric_code, "raw": raw, "type": clean(detected_type or selected.get("type", "")), "gtin": gtin, "debug": debug or {}}


def ensure_form_state(defaults):
    initial_values = {
        "stable_product": defaults.get("product", ""),
        "stable_brand": defaults.get("brand", ""),
        "stable_strength": defaults.get("strength", ""),
        "stable_form": defaults.get("form", ""),
        "stable_expiry": "",
        "stable_lot": "",
    }
    for key, value in initial_values.items():
        st.session_state.setdefault(key, clean(value))


def apply_defaults_from_existing_code(code, rows, defaults):
    code = clean(code)
    if not code or rows.empty or st.session_state.get("stable_defaults_code") == code:
        return
    for key, value in {
        "stable_product": defaults.get("product", ""),
        "stable_brand": defaults.get("brand", ""),
        "stable_strength": defaults.get("strength", ""),
        "stable_form": defaults.get("form", ""),
    }.items():
        if clean(value):
            st.session_state[key] = clean(value)
    st.session_state["stable_defaults_code"] = code


def suggestion_rows(suggestions):
    rows = [
        {"Πεδίο": "Όνομα", "Πρόταση": clean(suggestions.get("product", ""))},
        {"Πεδίο": "Μάρκα", "Πρόταση": clean(suggestions.get("brand", ""))},
        {"Πεδίο": "Περιεκτικότητα", "Πρόταση": clean(suggestions.get("strength", ""))},
        {"Πεδίο": "Μορφή", "Πρόταση": clean(suggestions.get("form", ""))},
        {"Πεδίο": "Λήξη", "Πρόταση": clean(suggestions.get("expiry", ""))},
        {"Πεδίο": "Lot", "Πρόταση": clean(suggestions.get("lot", ""))},
    ]
    return [row for row in rows if row["Πρόταση"]]


def apply_photo_suggestions_to_form(suggestions):
    if st.session_state.get("applied_photo_suggestion_key") == suggestions.get("key"):
        return
    for key, value in {
        "stable_product": suggestions.get("product", ""),
        "stable_brand": suggestions.get("brand", ""),
        "stable_strength": suggestions.get("strength", ""),
        "stable_form": suggestions.get("form", ""),
        "stable_expiry": suggestions.get("expiry", ""),
        "stable_lot": suggestions.get("lot", ""),
    }.items():
        if clean(value):
            st.session_state[key] = clean(value)
    st.session_state["applied_photo_suggestion_key"] = suggestions.get("key")


def product_initial(value):
    text = up(value)
    for ch in text:
        if ch.isalpha():
            return ch
        if ch.isdigit():
            return "0-9"
    return ""


def make_row(code, product, brand, category, strength, form, expiry, lot, location_id, qty, note, front_photo_url, qr_photo_url, scan_type="", scan_raw=""):
    code_type = "GTIN" if len(code) == 14 else "Barcode"
    barcode = "" if code_type == "GTIN" else code
    gtin = code if code_type == "GTIN" else ""
    raw_type = clean(scan_type)
    raw_value = clean(scan_raw)
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "TransactionId": str(uuid.uuid4()), "Timestamp": now, "Ημερομηνία": now,
        "CodeType": code_type, "CodeValue": code, "Barcode": barcode, "PCCode": "", "GTIN": gtin,
        "SerialNumber": "", "LotNumber": up(lot), "ExpiryDate": clean(expiry),
        "QRRawData": raw_value if raw_type == "QR" else "",
        "DataMatrixRawData": raw_value if raw_type == "DataMatrix" else "",
        "Strength": up(strength), "DosageForm": up(form),
        "Μάρκα": up(brand), "Προϊόν": up(product), "Κατηγορία": clean(category),
        "LocationId": location_id, "Τοποθεσία": LOCATIONS.get(location_id, core.LOCATIONS.get(location_id, "")),
        "Κίνηση": "Παραλαβή (+)", "Ποσότητα": int(qty), "DeltaQty": int(qty),
        "FrontPhotoUrl": front_photo_url, "BackPhotoUrl": qr_photo_url, "Σημείωση": clean(note),
        "Voided": "", "VoidOf": "", "MovementKind": core.NORMAL,
    }


def make_shelf_row(item, location_id, source_note=""):
    product = clean(item.get("ProductName", ""))
    qty = int(pd.to_numeric(item.get("EstimatedQty", 1), errors="coerce") or 1)
    code = clean(item.get("BarcodeOrGTIN", ""))
    expiry = parse_expiry(item.get("ExpiryDate", "")) if clean(item.get("ExpiryDate", "")) else ""
    lot = clean(item.get("LotNumber", ""))
    strength = clean(item.get("Strength", ""))
    category = clean(item.get("Category", "")) or "Φάρμακο"
    note = " | ".join([p for p in ["shelf_photo_or_chatgpt_confirmed=true", clean(item.get("Notes", "")), source_note] if p])

    qr_raw = ""
    datamatrix_raw = ""
    if code.isdigit():
        code_type = "GTIN" if len(code) == 14 else "Barcode"
        barcode = "" if code_type == "GTIN" else code
        gtin = code if code_type == "GTIN" else ""
        code_value = code
    elif code:
        code_type = "QR"
        code_value = code
        barcode = ""
        gtin = ""
        qr_raw = code
        try:
            parsed = core.parse_machine_readable_fields(code)
            gtin = clean(parsed.get("gtin", ""))
            if gtin:
                code_type = "GTIN"
                code_value = gtin
        except Exception:
            pass
    else:
        code_type = "Internal"
        code_value = "PHOTO-" + base_db.stable_hash(product, strength, expiry, lot, location_id, size=12)
        barcode = ""
        gtin = ""

    return core.make_transaction(
        code_type=code_type, code_value=code_value, barcode=barcode, gtin=gtin,
        qr_raw_data=qr_raw, datamatrix_raw_data=datamatrix_raw,
        brand=product.split()[0] if product else "", product=product, category=category,
        location_id=int(location_id), movement="Φωτογραφία αποθέματος (+)",
        quantity=max(1, qty), delta=max(1, qty), lot_number=lot, expiry_date=expiry,
        strength=strength, dosage_form="", note=note, movement_kind=core.NORMAL,
    )


def normalize_draft_columns(df):
    aliases = {
        "προϊόν": "ProductName", "προιον": "ProductName", "product": "ProductName", "productname": "ProductName", "name": "ProductName", "όνομα": "ProductName", "ονομα": "ProductName",
        "ποσότητα": "EstimatedQty", "ποσοτητα": "EstimatedQty", "qty": "EstimatedQty", "quantity": "EstimatedQty", "estimatedqty": "EstimatedQty",
        "barcode": "BarcodeOrGTIN", "ean": "BarcodeOrGTIN", "gtin": "BarcodeOrGTIN", "qr": "BarcodeOrGTIN", "barcodeorgtín": "BarcodeOrGTIN", "barcodeorgtIN": "BarcodeOrGTIN", "barcodeorgt": "BarcodeOrGTIN", "barcodeorgtin": "BarcodeOrGTIN",
        "λήξη": "ExpiryDate", "ληξη": "ExpiryDate", "expiry": "ExpiryDate", "expirydate": "ExpiryDate", "ημερομηνία λήξης": "ExpiryDate", "ημερομηνια ληξης": "ExpiryDate",
        "lot": "LotNumber", "lotnumber": "LotNumber", "παρτίδα": "LotNumber", "παρτιδα": "LotNumber",
        "strength": "Strength", "περιεκτικότητα": "Strength", "περιεκτικοτητα": "Strength",
        "category": "Category", "κατηγορία": "Category", "κατηγορια": "Category",
        "notes": "Notes", "σημειώσεις": "Notes", "σημειωσεις": "Notes",
    }
    renamed = {}
    for col in df.columns:
        key = re.sub(r"[^0-9a-zA-ZΑ-Ωα-ω ]", "", clean(col)).lower().replace(" ", "")
        spaced_key = re.sub(r"[^0-9a-zA-ZΑ-Ωα-ω ]", "", clean(col)).lower().strip()
        renamed[col] = aliases.get(key) or aliases.get(spaced_key) or clean(col)
    out = df.rename(columns=renamed).copy()
    for col in SHELF_DRAFT_COLUMNS:
        if col not in out.columns:
            if col == "confirm":
                out[col] = False
            elif col == "EstimatedQty":
                out[col] = 1
            elif col == "Category":
                out[col] = "Φάρμακο"
            elif col == "Confidence":
                out[col] = "chatgpt"
            elif col == "SourcePhoto":
                out[col] = "ChatGPT paste"
            elif col == "Notes":
                out[col] = "draft_from_chatgpt_vision"
            else:
                out[col] = ""
    out["ProductName"] = out["ProductName"].map(up)
    out["EstimatedQty"] = pd.to_numeric(out["EstimatedQty"], errors="coerce").fillna(1).astype(int).clip(lower=1)
    return out[SHELF_DRAFT_COLUMNS]


def parse_chatgpt_inventory_text(text):
    raw = clean(text)
    if not raw:
        return pd.DataFrame(columns=SHELF_DRAFT_COLUMNS)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]

    table_lines = [line for line in lines if "|" in line]
    if table_lines:
        rows = []
        for line in table_lines:
            parts = [clean(part) for part in line.strip("|").split("|")]
            if not parts or all(re.fullmatch(r"-+", p.replace(" ", "")) for p in parts):
                continue
            rows.append(parts)
        if rows:
            header = rows[0]
            body = rows[1:]
            if len(header) < 2 or not any(re.search(r"product|προϊόν|προιον|όνομα|ονομα", h, re.I) for h in header):
                header = ["ProductName", "EstimatedQty", "BarcodeOrGTIN", "ExpiryDate", "LotNumber", "Strength", "Category", "Notes"][: max(len(r) for r in rows)]
                body = rows
            width = len(header)
            fixed = [row + [""] * (width - len(row)) if len(row) < width else row[:width] for row in body]
            return normalize_draft_columns(pd.DataFrame(fixed, columns=header))

    for sep in ("\t", ";", ","):
        try:
            df = pd.read_csv(io.StringIO(raw), sep=sep)
            if len(df.columns) > 1:
                return normalize_draft_columns(df)
        except Exception:
            pass

    rows = []
    for line in lines:
        parts = [clean(p) for p in re.split(r"\s{2,}|\t|;", line) if clean(p)]
        if len(parts) == 1:
            match = re.match(r"(.+?)\s+[xΧ]?(\d+)$", parts[0])
            if match:
                rows.append({"ProductName": match.group(1), "EstimatedQty": match.group(2)})
            else:
                rows.append({"ProductName": parts[0], "EstimatedQty": 1})
        else:
            rows.append({"ProductName": parts[0], "EstimatedQty": parts[1]})
    return normalize_draft_columns(pd.DataFrame(rows))


def stock_table(data):
    display_cols = ["Barcode", "GTIN", "Προϊόν", "Μάρκα", "Κατηγορία", "Strength", "DosageForm", "ExpiryDate", "LotNumber", "LocationId", "Τοποθεσία", "Stock", "Αρχικό"]
    if data.empty:
        return pd.DataFrame(columns=display_cols)
    df = data.copy(deep=True)
    for col in core.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[~df["Voided"].astype(str).str.lower().isin(TRUE_VALUES)].copy()
    df["DeltaQty"] = pd.to_numeric(df["DeltaQty"], errors="coerce").fillna(0).astype(int)
    df["LocationId"] = pd.to_numeric(df["LocationId"], errors="coerce").fillna(-1).astype(int)
    for col in ["Barcode", "GTIN", "Προϊόν", "Μάρκα", "Κατηγορία", "Strength", "DosageForm", "ExpiryDate", "LotNumber", "Τοποθεσία"]:
        df[col] = df[col].fillna("").astype(str)
    df["Τοποθεσία"] = df.apply(lambda r: LOCATIONS.get(int(r["LocationId"]), clean(r["Τοποθεσία"]) or "Άγνωστη"), axis=1)
    group_cols = ["Barcode", "GTIN", "Προϊόν", "Μάρκα", "Κατηγορία", "Strength", "DosageForm", "ExpiryDate", "LotNumber", "LocationId", "Τοποθεσία"]
    stock = df.groupby(group_cols, dropna=False, as_index=False)["DeltaQty"].sum().rename(columns={"DeltaQty": "Stock"})
    stock = stock[stock["Stock"] != 0].copy()
    stock["Αρχικό"] = stock["Προϊόν"].map(product_initial)
    stock["SortName"] = stock["Προϊόν"].map(up)
    return stock.sort_values(["LocationId", "SortName", "ExpiryDate", "LotNumber"])


def filter_stock(stock, query, location_choice, initial_choice):
    result = stock.copy(deep=True)
    if location_choice != "Όλες":
        location_id = int(location_choice.split("-", 1)[0].strip())
        result = result[result["LocationId"] == location_id]
    if initial_choice != "Όλα":
        result = result[result["Αρχικό"] == initial_choice]
    q = clean(query).lower()
    if q:
        if len(q) == 1 and q.isalpha():
            letter = q.upper()
            result = result[result["Προϊόν"].astype(str).map(up).str.startswith(letter) | result["Μάρκα"].astype(str).map(up).str.startswith(letter)]
        else:
            mask = pd.Series(False, index=result.index)
            for col in ["Barcode", "GTIN", "Προϊόν", "Μάρκα", "Κατηγορία", "Strength", "DosageForm", "ExpiryDate", "LotNumber", "Τοποθεσία"]:
                mask |= result[col].astype(str).str.lower().str.contains(q, regex=False, na=False)
            result = result[mask]
    return result.sort_values(["LocationId", "SortName", "ExpiryDate", "LotNumber"])


def entry_tab(data):
    code = st.text_input("Barcode / GTIN (κωδικός προϊόντος)", key="stable_code")
    effective_code = clean(code)
    scan_result = {"code": "", "raw": "", "type": "", "gtin": "", "debug": {}}
    rows = stock_by_code(data, code)
    defaults = product_defaults(rows)
    ensure_form_state(defaults)
    apply_defaults_from_existing_code(code, rows, defaults)

    if clean(code) and not rows.empty:
        stock = pd.to_numeric(rows["DeltaQty"], errors="coerce").fillna(0).astype(int).sum()
        st.success(f"Βρέθηκε τοπικά: {defaults['product']} | stock κινήσεων: {stock}")
        st.dataframe(rows[["Προϊόν", "Μάρκα", "ExpiryDate", "LotNumber", "Τοποθεσία", "DeltaQty"]], hide_index=True, use_container_width=True)
    elif clean(code):
        st.info("Δεν υπάρχει τοπικά. Συμπλήρωσε χειροκίνητα.")

    col1, col2 = st.columns(2)
    with col1:
        front_photo = st.file_uploader("Μπροστινή φωτογραφία προϊόντος (προαιρετική OCR πρόταση ονόματος)", type=["jpg", "jpeg", "png"], key="front_photo_uploader")
        if front_photo:
            st.image(front_photo, caption="Μπροστινή φωτογραφία", width=260)
    with col2:
        qr_photo = st.file_uploader("Φωτογραφία QR / barcode / λήξης (προαιρετική ανάγνωση)", type=["jpg", "jpeg", "png"], key="qr_photo_uploader")
        scan_result = scan_code_from_photo(qr_photo) if qr_photo else scan_result
        if qr_photo:
            st.image(qr_photo, caption="Φωτογραφία QR / barcode / λήξης", width=260)
            if scan_result.get("code"):
                scanned_code = clean(scan_result["code"])
                st.success(f"Διαβάστηκε κωδικός: {scanned_code} ({scan_result.get('type') or 'code'})")
                current_code = clean(code)
                if not current_code:
                    effective_code = scanned_code
                    st.info(f"Θα χρησιμοποιηθεί αυτόματα στην αποθήκευση: {scanned_code}")
                elif current_code != scanned_code:
                    if st.checkbox("Χρήση κωδικού από τη φωτογραφία αντί για τον γραμμένο", key="use_photo_code"):
                        effective_code = scanned_code
                        st.info(f"Θα αποθηκευτεί με κωδικό φωτογραφίας: {scanned_code}")
            else:
                error = clean(scan_result.get("debug", {}).get("error", ""))
                st.warning(error or "Δεν διαβάστηκε καθαρός QR / barcode από τη φωτογραφία. Δοκίμασε πιο κοντινή και καθαρή λήψη.")

    if clean(effective_code):
        effective_rows = stock_by_code(data, effective_code)
        if not effective_rows.empty and (clean(effective_code) != clean(code) or rows.empty):
            effective_defaults = product_defaults(effective_rows)
            apply_defaults_from_existing_code(effective_code, effective_rows, effective_defaults)
            st.success(f"Βρέθηκε προϊόν από τον κωδικό φωτογραφίας: {effective_defaults['product']}")

    if front_photo or qr_photo:
        with st.spinner("Διαβάζω πιθανές πληροφορίες από τις φωτογραφίες..."):
            suggestions = photo_ai.suggest_fields(core, front_photo, qr_photo, scan_result)
        rows_for_display = suggestion_rows(suggestions)
        if rows_for_display:
            st.info("Βρέθηκαν προτάσεις από τις φωτογραφίες. Έλεγξέ τες πριν αποθήκευση.")
            st.dataframe(pd.DataFrame(rows_for_display), hide_index=True, use_container_width=True)
            if st.checkbox("Χρήση προτάσεων φωτογραφίας στα πεδία", value=False, key="use_photo_suggestions"):
                apply_photo_suggestions_to_form(suggestions)
        else:
            st.caption("Δεν βρέθηκαν καθαρές προτάσεις για όνομα, λήξη ή lot από τις φωτογραφίες.")

    options = CATEGORIES if defaults["category"] in CATEGORIES else [defaults["category"], *CATEGORIES]
    with st.form("save"):
        product = st.text_input("Όνομα προϊόντος", key="stable_product")
        brand = st.text_input("Μάρκα / Εταιρεία", key="stable_brand")
        category = st.selectbox("Κατηγορία", options)
        strength = st.text_input("Περιεκτικότητα (π.χ. 1000mg, 2000 IU, SPF50)", key="stable_strength")
        form = st.text_input("Μορφή προϊόντος (π.χ. κάψουλες, ταμπλέτες, κρέμα, σπρέι)", key="stable_form")
        expiry = st.text_input("Ημερομηνία λήξης", help="YYYY-MM-DD, DD/MM/YYYY ή MM/YYYY", key="stable_expiry")
        no_expiry = st.checkbox("Το προϊόν δεν έχει ημερομηνία λήξης")
        lot = st.text_input("Lot / Παρτίδα", key="stable_lot")
        location_label = st.selectbox("Τοποθεσία", [f"{k} - {v}" for k, v in LOCATIONS.items()], index=2)
        qty = st.number_input("Ποσότητα προσθήκης", min_value=1, value=1, step=1)
        note = st.text_input("Σημείωση")
        confirm = st.checkbox("Επιβεβαιώνω τα στοιχεία")
        submitted = st.form_submit_button("✅ Αποθήκευση + stock")
    if submitted:
        try:
            raw_code = clean(effective_code)
            if not raw_code.isdigit():
                raise core.InventoryError("Χρειάζεται αριθμητικό Barcode ή GTIN.")
            if not clean(product):
                raise core.InventoryError("Βάλε όνομα προϊόντος.")
            if not confirm:
                raise core.InventoryError("Επιβεβαίωσε τα στοιχεία.")
            expiry_value = parse_expiry(expiry) if clean(expiry) else ""
            if not expiry_value and not no_expiry:
                raise core.InventoryError("Συμπλήρωσε ημερομηνία λήξης ή επίλεξε ότι δεν έχει λήξη.")
            front_photo_url, front_photo_note = encode_uploaded_photo(front_photo, "front")
            qr_photo_url, qr_photo_note = encode_uploaded_photo(qr_photo, "qr")
            location_id = int(location_label.split("-", 1)[0].strip())
            note_parts = [clean(note), f"expiry_date={expiry_value}" if expiry_value else "no_expiry=true", f"scan_type={scan_result.get('type', '')}" if scan_result.get("type") else "", f"scan_raw={scan_result.get('raw', '')}" if scan_result.get("raw") else "", "photo_suggestions_used=true" if st.session_state.get("use_photo_suggestions") else "", front_photo_note, qr_photo_note]
            row = make_row(raw_code, product, brand, category, strength, form, expiry_value, lot, location_id, qty, " | ".join([p for p in note_parts if p]), front_photo_url, qr_photo_url, scan_result.get("type", ""), scan_result.get("raw", ""))
            core.append_stock_transaction(core.worksheet(), row)
            try:
                base_db.upsert_product_from_transaction(core, row)
            except Exception:
                pass
            core.invalidate_data_cache()
            st.success("Αποθηκεύτηκε.")
            st.rerun()
        except core.InventoryError as exc:
            st.error(str(exc))


def shelf_photo_tab(data):
    st.subheader("📸 Φωτογραφία αποθέματος")
    st.caption("Καλύτερη ροή: ανεβάζεις φωτογραφίες εδώ στη συνομιλία, παίρνεις καθαρό πίνακα από ChatGPT, τον κολλάς εδώ, και μετά περνάς ανά κουτί μόνο barcode/QR και λήξη πριν το τελικό OK.")

    location_label = st.selectbox("Τοποθεσία αποθέματος", [f"{k} - {v}" for k, v in LOCATIONS.items()], index=2, key="shelf_location")
    location_id = int(location_label.split("-", 1)[0].strip())

    pasted = st.text_area(
        "Επικόλληση πίνακα από ChatGPT",
        height=180,
        placeholder="Π.χ. | ProductName | EstimatedQty | BarcodeOrGTIN | ExpiryDate | LotNumber |\n| BRIVIACT 100MG | 5 | | | |",
        key="chatgpt_shelf_paste",
    )
    c_paste, c_clear = st.columns(2)
    with c_paste:
        if st.button("📥 Φόρτωση πίνακα ChatGPT", key="load_chatgpt_shelf_table"):
            draft_df = parse_chatgpt_inventory_text(pasted)
            st.session_state["shelf_draft_df"] = draft_df
            st.session_state["shelf_debug"] = [{"source_photo": "ChatGPT paste", "ocr_available": "not_used", "lines": pasted.splitlines(), "errors": []}]
            st.success(f"Φορτώθηκαν {len(draft_df)} γραμμές από ChatGPT. Τώρα περνάς barcode/QR και λήξη ανά κουτί/γραμμή. Εδώ αρχίζει η ανθρώπινη επιμέλεια, δυστυχώς ακόμα απαραίτητη.")
    with c_clear:
        if st.button("🧹 Καθαρισμός draft", key="clear_shelf_draft"):
            st.session_state.pop("shelf_draft_df", None)
            st.session_state.pop("shelf_debug", None)
            st.rerun()

    with st.expander("Εναλλακτικό OCR μέσα από την εφαρμογή, χαμηλότερη αξιοπιστία", expanded=False):
        uploaded_files = st.file_uploader("Φωτογραφίες ραφιού / αποθέματος", type=["jpg", "jpeg", "png"], accept_multiple_files=True, key="shelf_photo_uploader")
        if uploaded_files:
            st.image(uploaded_files, width=180)
            if st.button("Ανάλυση φωτογραφιών με OCR", key="analyze_shelf_photos"):
                with st.spinner("Διαβάζω ονόματα, πιθανές ποσότητες, barcode/QR και ημερομηνίες..."):
                    draft_df, debug = shelf_ai.suggest_shelf_inventory(core, uploaded_files)
                st.session_state["shelf_draft_df"] = draft_df
                st.session_state["shelf_debug"] = debug
                st.success(f"Βρέθηκαν {len(draft_df)} πιθανές γραμμές. Μην το πιστέψεις τυφλά, είναι OCR, όχι φαρμακοποιός.")

    draft_df = st.session_state.get("shelf_draft_df")
    if draft_df is not None:
        st.info("Διόρθωσε ποσότητες και μετά πήγαινε ανά κουτί για Barcode/QR και ημερομηνία λήξης. Τσέκαρε OK μόνο στις τελικές γραμμές.")
        edited = st.data_editor(
            draft_df,
            hide_index=True,
            use_container_width=True,
            num_rows="dynamic",
            key="shelf_draft_editor",
            column_config={
                "confirm": st.column_config.CheckboxColumn("OK", help="Μόνο τα τσεκαρισμένα αποθηκεύονται"),
                "ProductName": st.column_config.TextColumn("Προϊόν", required=True),
                "EstimatedQty": st.column_config.NumberColumn("Ποσότητα", min_value=1, step=1),
                "BarcodeOrGTIN": st.column_config.TextColumn("Barcode / GTIN / QR"),
                "ExpiryDate": st.column_config.TextColumn("Λήξη"),
                "LotNumber": st.column_config.TextColumn("Lot"),
                "Strength": st.column_config.TextColumn("Περιεκτικότητα"),
                "Category": st.column_config.TextColumn("Κατηγορία"),
            },
        )
        st.caption("Αν ίδια προϊόντα έχουν διαφορετική λήξη, κάνε ξεχωριστές γραμμές. Ναι, βαρετό. Όχι, δεν είναι προαιρετικό αν θες σωστές λήξεις.")
        final_ok = st.checkbox("Τελικό ΟΚ: έλεγξα barcode/QR, λήξη και ποσότητες", key="shelf_final_ok")
        if st.button("✅ Αποθήκευση επιβεβαιωμένων στο stock", key="save_shelf_stock"):
            if not final_ok:
                st.error("Τσέκαρε πρώτα το τελικό ΟΚ. Τα φάρμακα δεν είναι πεδίο για YOLO αποθήκευση, ευτυχώς.")
            else:
                saved = 0
                skipped = 0
                ws = core.worksheet()
                for _, item in edited.iterrows():
                    if not bool(item.get("confirm", False)) or not clean(item.get("ProductName", "")):
                        skipped += 1
                        continue
                    try:
                        row = make_shelf_row(item, location_id, source_note="source=chatgpt_or_shelf_photo_review")
                        core.append_stock_transaction(ws, row)
                        try:
                            base_db.upsert_product_from_transaction(core, row)
                        except Exception:
                            pass
                        saved += 1
                    except Exception as exc:
                        st.warning(f"Δεν αποθηκεύτηκε γραμμή {clean(item.get('ProductName', ''))}: {exc}")
                core.invalidate_data_cache()
                st.success(f"Αποθηκεύτηκαν {saved} γραμμές. Παραλείφθηκαν {skipped}.")
                st.rerun()

        with st.expander("Debug / γραμμές εισαγωγής", expanded=False):
            for dbg in st.session_state.get("shelf_debug", []):
                st.write(f"**{dbg.get('source_photo', '')}**")
                st.caption("OCR available: " + clean(dbg.get("ocr_available", "")))
                if dbg.get("errors"):
                    st.warning(" | ".join(map(str, dbg.get("errors", []))))
                lines = dbg.get("lines", [])
                if lines:
                    st.text("\n".join(lines[:120]))


def stock_tab(data):
    stock = stock_table(data)
    st.subheader("📦 Stock ανά τοποθεσία")
    st.caption("Για το stock πάνω, άσε επιλεγμένο το Πάνω / Επίπεδο 1. Τα προϊόντα εμφανίζονται αλφαβητικά, γιατί το χάος έχει ήδη αρκετούς εκπροσώπους.")
    choices = ["2 - Πάνω / Επίπεδο 1", "Όλες", "0 - Αποθήκη", "1 - Κάτω / Κύριο Κτήριο"]
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        location_choice = st.selectbox("Τοποθεσία", choices)
    with c2:
        initial_choice = st.selectbox("Αρχικό προϊόντος", INITIAL_CHOICES)
    with c3:
        query = st.text_input("Αναζήτηση", placeholder="π.χ. D για Depon, depon, 520..., κάψουλες, παρτίδα")
    filtered = filter_stock(stock, query, location_choice, initial_choice)
    m1, m2 = st.columns(2)
    m1.metric("Batches", len(filtered))
    m2.metric("Σύνολο τεμαχίων", int(filtered["Stock"].sum()) if not filtered.empty else 0)
    if filtered.empty:
        st.info("Δεν βρέθηκε stock με αυτά τα φίλτρα.")
        return
    st.dataframe(filtered[["Αρχικό", "Barcode", "GTIN", "Προϊόν", "Μάρκα", "Κατηγορία", "Strength", "DosageForm", "ExpiryDate", "LotNumber", "Τοποθεσία", "Stock"]], hide_index=True, use_container_width=True)


def expiry_tab(data):
    stock = stock_table(data)
    if stock.empty:
        st.info("Δεν υπάρχει stock.")
        return
    today = date.today()
    frame = stock.copy(deep=True)
    frame["DaysToExpiry"] = pd.to_datetime(frame["ExpiryDate"], errors="coerce").dt.date.map(lambda exp: (exp - today).days if pd.notna(exp) else None)
    expiring = frame[frame["DaysToExpiry"].notna() & (frame["DaysToExpiry"] <= 90)].sort_values("DaysToExpiry")
    no_expiry = frame[frame["ExpiryDate"].astype(str).str.strip().eq("")]
    st.subheader("⚠️ Λήξεις")
    with st.expander(f"Ληγμένα / λήγουν σε 90 ημέρες ({len(expiring)})", expanded=True):
        st.dataframe(expiring[["Προϊόν", "Μάρκα", "ExpiryDate", "DaysToExpiry", "LotNumber", "Τοποθεσία", "Stock"]], hide_index=True, use_container_width=True)
    with st.expander(f"Χωρίς λήξη ({len(no_expiry)})", expanded=False):
        st.dataframe(no_expiry[["Προϊόν", "Μάρκα", "LotNumber", "Τοποθεσία", "Stock"]], hide_index=True, use_container_width=True)


def base_tab(data):
    st.subheader("🧱 Βάση προϊόντων")
    st.caption("Η βάση κρατάει προϊόντα, αντιστοιχίσεις προμηθευτή, γραμμές τιμολογίων και πωλήσεις. Το stock συνεχίζει να βγαίνει από κινήσεις, όπως πρέπει, γιατί τα μαγικά κελιά είναι για μάγους και κακά ERP.")
    inferred_products = base_db.product_rows_from_transactions(data)
    products_df = base_db.read_sheet_df(core, "Products", base_db.PRODUCT_COLUMNS)
    mappings_df = base_db.read_sheet_df(core, "SupplierMappings", base_db.SUPPLIER_MAPPING_COLUMNS)
    m1, m2, m3 = st.columns(3)
    m1.metric("Προϊόντα από κινήσεις", len(inferred_products))
    m2.metric("Products sheet", len(products_df))
    m3.metric("Supplier mappings", len(mappings_df))
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Δημιουργία / έλεγχος φύλλων βάσης"):
            try:
                sizes = base_db.ensure_base_sheets(core)
                st.success("Έτοιμα τα φύλλα βάσης: " + ", ".join(f"{name}: {count}" for name, count in sizes.items()))
            except Exception as exc:
                st.error(f"Δεν μπόρεσα να φτιάξω τα φύλλα βάσης: {exc}")
    with c2:
        if st.button("Συγχρονισμός Products από κινήσεις"):
            try:
                result = base_db.sync_products_from_transactions(core, data)
                st.success(f"Προστέθηκαν {result['added']} νέα προϊόντα στη βάση. Υποψήφια: {result['candidates']}, υπήρχαν ήδη: {result['existing']}.")
            except Exception as exc:
                st.error(f"Δεν μπόρεσα να συγχρονίσω Products: {exc}")
    with st.expander("Σχήμα βάσης", expanded=False):
        st.dataframe(base_db.schema_overview_df(), hide_index=True, use_container_width=True)
    with st.expander("Products sheet", expanded=True):
        if products_df.empty:
            st.info("Δεν υπάρχει ακόμα Products sheet ή είναι άδειο. Πάτα πρώτα δημιουργία/συγχρονισμό.")
        else:
            st.dataframe(products_df, hide_index=True, use_container_width=True)
    with st.expander("Προϊόντα που προκύπτουν από τις κινήσεις", expanded=False):
        if inferred_products:
            st.dataframe(pd.DataFrame(inferred_products), hide_index=True, use_container_width=True)
        else:
            st.info("Δεν υπάρχουν αρκετές κινήσεις για να προκύψει μητρώο προϊόντων.")


def main():
    st.set_page_config(page_title="Αποθήκη - Απλή Καταχώρηση", page_icon="📦", layout="wide")
    st.title("📦 Αποθήκη - Απλή Καταχώρηση")
    st.caption("Καταχώρηση + stock ανά τοποθεσία. Οι φωτογραφίες δίνουν προτάσεις, αλλά η αποθήκευση θέλει δική σου επιβεβαίωση.")
    data = load_data()
    tab_entry, tab_shelf, tab_stock, tab_expiry, tab_base = st.tabs(["➕ Καταχώρηση", "📸 Φωτογραφία αποθέματος", "📦 Stock / Πάνω", "⚠️ Λήξεις", "🧱 Βάση"])
    with tab_entry:
        entry_tab(data)
    with tab_shelf:
        shelf_photo_tab(data)
    with tab_stock:
        stock_tab(data)
    with tab_expiry:
        expiry_tab(data)
    with tab_base:
        base_tab(data)


if __name__ == "__main__":
    main()
