import uuid
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

import app_inventory_search as core
import inventory_base as base_db

LOCATIONS = {0: "Αποθήκη", 1: "Κάτω / Κύριο Κτήριο", 2: "Πάνω / Επίπεδο 1"}
TRUE_VALUES = {"true", "1", "yes", "y", "ναι", "nai"}


def clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def up(value: Any) -> str:
    return " ".join(clean(value).upper().split())


def load_transactions() -> pd.DataFrame:
    ws = core.worksheet()
    data, _ = core.load_data_cached(ws)
    frame = data.copy(deep=True)
    for column in core.COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame


def search_transactions(data: pd.DataFrame, query: str) -> pd.DataFrame:
    if data.empty or not clean(query):
        return data.iloc[0:0].copy()
    search = clean(query).lower()
    mask = pd.Series(False, index=data.index)
    for column in ["Προϊόν", "Μάρκα", "Barcode", "GTIN", "PCCode", "SerialNumber", "LotNumber", "ExpiryDate", "CodeValue"]:
        mask |= data[column].astype(str).str.lower().str.contains(search, regex=False, na=False)
    return data[mask].copy()


def stock_summary(rows: pd.DataFrame) -> pd.DataFrame:
    columns = ["Προϊόν", "Barcode", "GTIN", "PCCode", "ExpiryDate", "LotNumber", "LocationId", "Τοποθεσία", "Stock"]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    frame = rows.copy(deep=True)
    frame = frame[~frame["Voided"].astype(str).str.lower().isin(TRUE_VALUES)].copy()
    frame["DeltaQty"] = pd.to_numeric(frame["DeltaQty"], errors="coerce").fillna(0).astype(int)
    frame["LocationId"] = pd.to_numeric(frame["LocationId"], errors="coerce").fillna(-1).astype(int)
    for column in ["Προϊόν", "Barcode", "GTIN", "PCCode", "ExpiryDate", "LotNumber", "Τοποθεσία"]:
        frame[column] = frame[column].fillna("").astype(str)
    frame["Τοποθεσία"] = frame.apply(
        lambda row: LOCATIONS.get(int(row["LocationId"]), clean(row["Τοποθεσία"]) or "Άγνωστη"), axis=1
    )
    grouped = frame.groupby(
        ["Προϊόν", "Barcode", "GTIN", "PCCode", "ExpiryDate", "LotNumber", "LocationId", "Τοποθεσία"],
        dropna=False,
        as_index=False,
    )["DeltaQty"].sum().rename(columns={"DeltaQty": "Stock"})
    return grouped[grouped["Stock"] != 0].sort_values(["Προϊόν", "LocationId", "ExpiryDate"])


def build_identifier_row(product_name: str, barcode: str, gtin: str, pc_code: str, serial_number: str, strength: str, dosage_form: str, lot: str, expiry: str, location_id: int) -> dict[str, Any]:
    identifier = clean(gtin) or clean(barcode) or clean(pc_code)
    if gtin:
        code_type, code_value = "GTIN", clean(gtin)
    elif barcode:
        code_type, code_value = "Barcode", clean(barcode)
    elif pc_code:
        code_type, code_value = "PC", clean(pc_code)
    else:
        code_type = "Internal"
        code_value = "PRODUCT-" + base_db.stable_hash(product_name, strength, dosage_form, size=14)
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "TransactionId": "identifier-" + str(uuid.uuid4()),
        "Timestamp": now,
        "Ημερομηνία": now,
        "CodeType": code_type,
        "CodeValue": code_value,
        "Barcode": clean(barcode),
        "GTIN": clean(gtin),
        "PCCode": clean(pc_code),
        "SerialNumber": clean(serial_number),
        "LotNumber": clean(lot),
        "ExpiryDate": clean(expiry),
        "Strength": up(strength),
        "DosageForm": up(dosage_form),
        "Προϊόν": up(product_name),
        "Μάρκα": up(product_name).split()[0] if clean(product_name) else "",
        "Κατηγορία": "Φάρμακο",
        "LocationId": int(location_id),
        "Τοποθεσία": LOCATIONS[int(location_id)],
        "Σημείωση": f"identifier_only=true; identifier={identifier}",
    }


st.set_page_config(page_title="Αναζήτηση φαρμάκων", page_icon="🔎", layout="wide")
st.title("🔎 Αναζήτηση φαρμάκων και αναγνωριστικών")
st.caption("Αναζήτηση με όνομα, Barcode, GTIN, PC ή SN. Το PC χαρακτηρίζει το προϊόν, το SN το συγκεκριμένο κουτί. Απλό, παρότι τα κουτιά επιμένουν να τυπώνουν ολόκληρο μυθιστόρημα πίσω τους.")

try:
    data = load_transactions()
except Exception as exc:
    st.error(f"Δεν φορτώθηκαν οι κινήσεις: {exc}")
    data = pd.DataFrame(columns=core.COLUMNS)

query = st.text_input("Αναζήτηση", placeholder="π.χ. BUDECOL, 07640129622818 ή SN...")
if clean(query):
    matches = search_transactions(data, query)
    summary = stock_summary(matches)
    if summary.empty:
        st.warning("Δεν βρέθηκε ενεργό stock με αυτό το στοιχείο.")
    else:
        st.success(f"Βρέθηκαν {len(summary)} γραμμές stock.")
        st.dataframe(summary, hide_index=True, use_container_width=True)
    with st.expander(f"Κινήσεις που ταιριάζουν ({len(matches)})", expanded=False):
        display = ["Προϊόν", "Barcode", "GTIN", "PCCode", "SerialNumber", "ExpiryDate", "LotNumber", "Τοποθεσία", "DeltaQty", "Timestamp"]
        st.dataframe(matches[display] if not matches.empty else pd.DataFrame(columns=display), hide_index=True, use_container_width=True)

st.divider()
st.subheader("Συμπλήρωση PC / SN χωρίς νέα κίνηση stock")
st.caption("Χρησιμοποίησέ το όταν το προϊόν υπάρχει ήδη και θέλεις απλώς να προσθέσεις τα δύο αναγνωριστικά ή τη λήξη αργότερα.")
with st.form("identifier_form", clear_on_submit=True):
    product_name = st.text_input("Όνομα προϊόντος *")
    c1, c2 = st.columns(2)
    with c1:
        barcode = st.text_input("Barcode / EAN")
        gtin = st.text_input("GTIN")
        pc_code = st.text_input("PC / Product Code")
        serial_number = st.text_input("SN / Serial Number")
    with c2:
        strength = st.text_input("Περιεκτικότητα")
        dosage_form = st.text_input("Μορφή")
        lot = st.text_input("Lot")
        expiry = st.text_input("Λήξη")
        location_label = st.selectbox("Τοποθεσία αναφοράς", [f"{key} - {value}" for key, value in LOCATIONS.items()], index=2)
    confirm = st.checkbox("Επιβεβαιώνω ότι τα αναγνωριστικά αντιστοιχούν σε αυτό το προϊόν")
    submitted = st.form_submit_button("💾 Αποθήκευση αναγνωριστικών", use_container_width=True)

if submitted:
    try:
        if not clean(product_name):
            raise ValueError("Χρειάζεται όνομα προϊόντος.")
        if not any(clean(value) for value in [barcode, gtin, pc_code, serial_number]):
            raise ValueError("Χρειάζεται τουλάχιστον Barcode, GTIN, PC ή SN.")
        if not confirm:
            raise ValueError("Χρειάζεται επιβεβαίωση.")
        location_id = int(location_label.split("-", 1)[0].strip())
        row = build_identifier_row(product_name, barcode, gtin, pc_code, serial_number, strength, dosage_form, lot, expiry, location_id)
        changed = base_db.upsert_product_from_transaction(core, row)
        st.success("Τα αναγνωριστικά αποθηκεύτηκαν στη βάση." if changed else "Τα ίδια αναγνωριστικά υπήρχαν ήδη.")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

st.divider()
st.subheader("Product Master")
try:
    products = base_db.read_sheet_df(core, "Products", base_db.PRODUCT_COLUMNS)
    packages = base_db.read_sheet_df(core, "PackageIdentifiers", base_db.PACKAGE_IDENTIFIER_COLUMNS)
    c1, c2 = st.columns(2)
    if c1.button("Δημιουργία / έλεγχος φύλλων", use_container_width=True):
        base_db.ensure_base_sheets(core)
        st.success("Τα φύλλα βάσης είναι έτοιμα.")
        st.rerun()
    if c2.button("Συγχρονισμός από όλες τις κινήσεις", use_container_width=True):
        result = base_db.sync_products_from_transactions(core, data)
        st.success(f"Νέα: {result['added']}, ενημερωμένα: {result['updated']}, νέα SN/PC: {result['packages_added']}.")
        st.rerun()
    with st.expander(f"Προϊόντα ({len(products)})", expanded=True):
        st.dataframe(products, hide_index=True, use_container_width=True)
    with st.expander(f"Αναγνωριστικά συσκευασιών ({len(packages)})", expanded=False):
        st.dataframe(packages, hide_index=True, use_container_width=True)
except Exception as exc:
    st.error(f"Δεν φορτώθηκε η βάση προϊόντων: {exc}")
