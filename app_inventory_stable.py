import base64
import calendar
import io
from datetime import date, datetime
import uuid

import streamlit as st
import pandas as pd
from PIL import Image

import app_inventory_search as core

CATEGORIES = ["Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Ορθοπεδικό", "Βρεφικό", "Άλλο"]
MAX_PHOTO_CELL_CHARS = 45000


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
    match = __import__("re").fullmatch(r"(\d{1,2})[./-](\d{4})", text)
    if match:
        month, year = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, last_day).isoformat()
    raise core.InventoryError("Η λήξη πρέπει να είναι YYYY-MM-DD, DD/MM/YYYY ή MM/YYYY.")


def encode_front_photo(uploaded_file):
    if not uploaded_file:
        return "", ""
    image = Image.open(uploaded_file).convert("RGB")
    image.thumbnail((640, 640))
    for quality in (75, 65, 55, 45):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        payload = base64.b64encode(buffer.getvalue()).decode("ascii")
        data_url = f"data:image/jpeg;base64,{payload}"
        if len(data_url) <= MAX_PHOTO_CELL_CHARS:
            note = f"front_photo_saved=true; front_photo_chars={len(data_url)}; front_photo_quality={quality}"
            return data_url, note
    raise core.InventoryError("Η μπροστινή φωτογραφία είναι πολύ μεγάλη για το Google Sheet. Βάλε πιο κοντινή/κομμένη φωτογραφία.")


def make_row(code, product, brand, category, strength, form, expiry, lot, location_id, qty, note, front_photo_url):
    code_type = "GTIN" if len(code) == 14 else "Barcode"
    barcode = "" if code_type == "GTIN" else code
    gtin = code if code_type == "GTIN" else ""
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "TransactionId": str(uuid.uuid4()), "Timestamp": now, "Ημερομηνία": now,
        "CodeType": code_type, "CodeValue": code, "Barcode": barcode, "PCCode": "", "GTIN": gtin,
        "SerialNumber": "", "LotNumber": up(lot), "ExpiryDate": clean(expiry),
        "QRRawData": "", "DataMatrixRawData": "", "Strength": up(strength), "DosageForm": up(form),
        "Μάρκα": up(brand), "Προϊόν": up(product), "Κατηγορία": clean(category),
        "LocationId": location_id, "Τοποθεσία": core.LOCATIONS.get(location_id, ""),
        "Κίνηση": "Παραλαβή (+)", "Ποσότητα": int(qty), "DeltaQty": int(qty),
        "FrontPhotoUrl": front_photo_url, "BackPhotoUrl": "", "Σημείωση": clean(note),
        "Voided": "", "VoidOf": "", "MovementKind": core.NORMAL,
    }


def main():
    st.set_page_config(page_title="Αποθήκη Stable", page_icon="📦", layout="wide")
    st.title("📦 Αποθήκη Stable Mode")
    st.caption("Χωρίς OCR ή online lookup. Η μπροστινή φωτογραφία είναι μόνο reference.")
    data = load_data()
    code = st.text_input("Barcode / GTIN")
    rows = stock_by_code(data, code)
    defaults = product_defaults(rows)
    if clean(code) and not rows.empty:
        stock = pd.to_numeric(rows["DeltaQty"], errors="coerce").fillna(0).astype(int).sum()
        st.success(f"Βρέθηκε τοπικά: {defaults['product']} | stock κινήσεων: {stock}")
        st.dataframe(rows[["Προϊόν", "Μάρκα", "ExpiryDate", "LotNumber", "Τοποθεσία", "DeltaQty"]], hide_index=True, use_container_width=True)
    elif clean(code):
        st.info("Δεν υπάρχει τοπικά. Συμπλήρωσε χειροκίνητα.")

    front_photo = st.file_uploader("Μπροστινή φωτογραφία προϊόντος (προαιρετική, μόνο reference)", type=["jpg", "jpeg", "png"])
    if front_photo:
        st.image(front_photo, caption="Μπροστινή φωτογραφία reference", width=260)

    options = CATEGORIES if defaults["category"] in CATEGORIES else [defaults["category"], *CATEGORIES]
    with st.form("save"):
        product = st.text_input("Product name", value=defaults["product"])
        brand = st.text_input("Brand", value=defaults["brand"])
        category = st.selectbox("Κατηγορία", options)
        strength = st.text_input("Strength", value=defaults["strength"])
        form = st.text_input("Dosage form", value=defaults["form"])
        expiry = st.text_input("Ημερομηνία λήξης", help="YYYY-MM-DD, DD/MM/YYYY ή MM/YYYY")
        no_expiry = st.checkbox("Το προϊόν δεν έχει ημερομηνία λήξης")
        lot = st.text_input("Lot number")
        location_label = st.selectbox("Τοποθεσία", [f"{k} - {v}" for k, v in core.LOCATIONS.items()])
        qty = st.number_input("Quantity to add", min_value=1, value=1, step=1)
        note = st.text_input("Σημείωση")
        confirm = st.checkbox("Επιβεβαιώνω τα στοιχεία")
        submitted = st.form_submit_button("✅ Αποθήκευση + stock")
    if submitted:
        try:
            raw_code = clean(code)
            if not raw_code.isdigit():
                raise core.InventoryError("Χρειάζεται αριθμητικό Barcode ή GTIN.")
            if not clean(product):
                raise core.InventoryError("Βάλε όνομα προϊόντος.")
            if not confirm:
                raise core.InventoryError("Επιβεβαίωσε τα στοιχεία.")
            expiry_value = parse_expiry(expiry) if clean(expiry) else ""
            if not expiry_value and not no_expiry:
                raise core.InventoryError("Συμπλήρωσε ημερομηνία λήξης ή επίλεξε ότι δεν έχει λήξη.")
            front_photo_url, photo_note = encode_front_photo(front_photo)
            location_id = int(location_label.split("-", 1)[0].strip())
            note_parts = [clean(note), f"expiry_date={expiry_value}" if expiry_value else "no_expiry=true", photo_note]
            row = make_row(raw_code, product, brand, category, strength, form, expiry_value, lot, location_id, qty, " | ".join([p for p in note_parts if p]), front_photo_url)
            core.append_stock_transaction(core.worksheet(), row)
            core.invalidate_data_cache()
            st.success("Αποθηκεύτηκε.")
            st.rerun()
        except core.InventoryError as exc:
            st.error(str(exc))


if __name__ == "__main__":
    main()
