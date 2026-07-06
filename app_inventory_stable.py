from datetime import datetime
import uuid

import streamlit as st
import pandas as pd

import app_inventory_search as core

CATEGORIES = ["Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Ορθοπεδικό", "Βρεφικό", "Άλλο"]


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


def make_row(code, product, brand, category, strength, form, expiry, lot, location_id, qty, note):
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
        "FrontPhotoUrl": "", "BackPhotoUrl": "", "Σημείωση": clean(note),
        "Voided": "", "VoidOf": "", "MovementKind": core.NORMAL,
    }


def main():
    st.set_page_config(page_title="Αποθήκη Stable", page_icon="📦", layout="wide")
    st.title("📦 Αποθήκη Stable Mode")
    st.caption("Χωρίς φωτογραφίες, OCR ή online lookup. Βαρετό, άρα επιτέλους πιθανό να δουλεύει.")
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
    options = CATEGORIES if defaults["category"] in CATEGORIES else [defaults["category"], *CATEGORIES]
    with st.form("save"):
        product = st.text_input("Product name", value=defaults["product"])
        brand = st.text_input("Brand", value=defaults["brand"])
        category = st.selectbox("Κατηγορία", options)
        strength = st.text_input("Strength", value=defaults["strength"])
        form = st.text_input("Dosage form", value=defaults["form"])
        expiry = st.text_input("Expiry date ή άστο κενό αν δεν έχει")
        lot = st.text_input("Lot number")
        location_label = st.selectbox("Τοποθεσία", [f"{k} - {v}" for k, v in core.LOCATIONS.items()])
        qty = st.number_input("Quantity to add", min_value=1, value=1, step=1)
        note = st.text_input("Σημείωση")
        confirm = st.checkbox("Επιβεβαιώνω τα στοιχεία")
        submitted = st.form_submit_button("✅ Αποθήκευση + stock")
    if submitted:
        if not clean(code).isdigit():
            st.error("Χρειάζεται αριθμητικό Barcode ή GTIN.")
            return
        if not clean(product):
            st.error("Βάλε όνομα προϊόντος.")
            return
        if not confirm:
            st.error("Επιβεβαίωσε τα στοιχεία.")
            return
        location_id = int(location_label.split("-", 1)[0].strip())
        row = make_row(clean(code), product, brand, category, strength, form, expiry, lot, location_id, qty, note)
        core.append_stock_transaction(core.worksheet(), row)
        core.invalidate_data_cache()
        st.success("Αποθηκεύτηκε.")
        st.rerun()


if __name__ == "__main__":
    main()
