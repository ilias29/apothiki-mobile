import calendar
import io
import re
import unicodedata
from datetime import date, datetime
from typing import Any

import pandas as pd
import streamlit as st

import app_inventory_search as core
import inventory_base as base_db
import inventory_import as import_io

LOCATIONS = {0: "Αποθήκη", 1: "Κάτω / Κύριο Κτήριο", 2: "Πάνω / Επίπεδο 1"}
TRUE_VALUES = {"true", "1", "yes", "y", "ναι", "nai"}
CATALOG_COLUMNS = [
    "ProductId", "ProductName", "Brand", "BarcodeOrGTIN", "Category",
    "NetPrice", "VATRate", "GrossPrice", "UpdatedAt", "Source",
]
IMPORT_COLUMNS = [
    "confirm", "ProductName", "Brand", "BarcodeOrGTIN", "Category",
    "NetPrice", "VATRate", "GrossPrice", "Quantity", "ExpiryDate",
    "LotNumber", "SerialNumber", "QRRawData", "DataMatrixRawData",
    "Strength", "DosageForm", "Notes",
]
ALIASES = {
    "product": "ProductName", "productname": "ProductName", "name": "ProductName",
    "description": "ProductName", "προιον": "ProductName", "ονομα": "ProductName", "περιγραφη": "ProductName",
    "brand": "Brand", "company": "Brand", "εταιρεια": "Brand", "μαρκα": "Brand",
    "barcode": "BarcodeOrGTIN", "ean": "BarcodeOrGTIN", "gtin": "BarcodeOrGTIN", "barcodeorgtin": "BarcodeOrGTIN", "κωδικος": "BarcodeOrGTIN",
    "category": "Category", "κατηγορια": "Category",
    "price": "Price", "τιμη": "Price", "unitprice": "Price",
    "netprice": "NetPrice", "καθαρητιμη": "NetPrice", "τιμηχωριςφπα": "NetPrice",
    "grossprice": "GrossPrice", "retailprice": "GrossPrice", "λιανικη": "GrossPrice", "τελικητιμη": "GrossPrice", "τιμημεφπα": "GrossPrice",
    "vat": "VATRate", "vatrate": "VATRate", "φπα": "VATRate",
    "quantity": "Quantity", "qty": "Quantity", "stock": "Quantity", "ποσοτητα": "Quantity", "totalquantity": "Quantity", "estimatedqty": "Quantity",
    "expiry": "ExpiryDate", "expirydate": "ExpiryDate", "exp": "ExpiryDate", "ληξη": "ExpiryDate", "ημερομηνιαληξης": "ExpiryDate",
    "lot": "LotNumber", "lotnumber": "LotNumber", "παρτιδα": "LotNumber",
    "serial": "SerialNumber", "serialnumber": "SerialNumber", "sn": "SerialNumber",
    "qr": "QRRawData", "qrrawdata": "QRRawData", "datamatrix": "DataMatrixRawData", "datamatrixrawdata": "DataMatrixRawData",
    "strength": "Strength", "περιεκτικοτητα": "Strength", "dosageform": "DosageForm", "form": "DosageForm", "μορφη": "DosageForm",
    "notes": "Notes", "note": "Notes", "σημειωση": "Notes", "confirm": "confirm", "ok": "confirm",
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


def up(value: Any) -> str:
    return " ".join(clean(value).upper().split())


def key(value: Any) -> str:
    text = unicodedata.normalize("NFD", clean(value).lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^0-9a-zα-ω]", "", text)


def number(value: Any, default=None):
    text = clean(value).replace("€", "").replace("%", "").replace(" ", "")
    if not text:
        return default
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def code(value: Any) -> str:
    text = clean(value)
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text


def expiry(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date().isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})[./-](\d{4})", text)
    if match:
        month, year = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    return text


def read_file(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    if filename.lower().endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object)
    for encoding in ("utf-8-sig", "utf-8", "cp1253"):
        try:
            return {"CSV": pd.read_csv(io.BytesIO(data), sep=None, engine="python", encoding=encoding, dtype=object)}
        except UnicodeDecodeError:
            pass
    raise ValueError("Δεν διαβάστηκε το CSV.")


def normalize(frame: pd.DataFrame, vat_default: float, generic_price_gross: bool) -> pd.DataFrame:
    out = frame.rename(columns={column: ALIASES.get(key(column), clean(column)) for column in frame.columns}).copy()
    if out.columns.duplicated().any():
        raise ValueError("Υπάρχουν διπλές στήλες μετά την αντιστοίχιση.")
    defaults = {column: "" for column in IMPORT_COLUMNS}
    defaults.update({"confirm": True, "Category": "Καλλυντικό", "VATRate": vat_default, "Quantity": 0})
    for column, value in defaults.items():
        if column not in out.columns:
            out[column] = value
    if "Price" in out.columns:
        target = "GrossPrice" if generic_price_gross else "NetPrice"
        mask = out[target].map(clean).eq("")
        out.loc[mask, target] = out.loc[mask, "Price"]
    out["ProductName"] = out["ProductName"].map(up)
    out["Brand"] = out["Brand"].map(up)
    out["BarcodeOrGTIN"] = out["BarcodeOrGTIN"].map(code)
    out["ExpiryDate"] = out["ExpiryDate"].map(expiry)
    out["confirm"] = out["confirm"].map(lambda value: key(value) in {"1", "true", "yes", "y", "ναι", "nai", "ok", "x"})
    vat = out["VATRate"].map(lambda value: number(value, vat_default)).fillna(vat_default).map(lambda value: value * 100 if 0 < value <= 1 else value)
    net = out["NetPrice"].map(number)
    gross = out["GrossPrice"].map(number)
    out["VATRate"] = vat.round(2)
    out["NetPrice"] = [round(n if n is not None else (g / (1 + v / 100) if g is not None else 0), 2) for n, g, v in zip(net, gross, vat)]
    out["GrossPrice"] = [round(g if g is not None else (n * (1 + v / 100) if n is not None else 0), 2) for n, g, v in zip(net, gross, vat)]
    out["Quantity"] = pd.to_numeric(out["Quantity"], errors="coerce").fillna(0).round().astype(int).clip(lower=0)
    for column in ["Category", "LotNumber", "SerialNumber", "QRRawData", "DataMatrixRawData", "Strength", "DosageForm", "Notes"]:
        out[column] = out[column].map(clean)
    return out[(out["ProductName"].ne("")) | (out["BarcodeOrGTIN"].ne(""))][IMPORT_COLUMNS].reset_index(drop=True)


def catalog_ws():
    return base_db.ensure_worksheet(core, "Catalog", CATALOG_COLUMNS)


def load_catalog() -> pd.DataFrame:
    frame = pd.DataFrame(catalog_ws().get_all_records())
    for column in CATALOG_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[CATALOG_COLUMNS]


def save_catalog(rows: pd.DataFrame, source: str) -> tuple[int, int]:
    ws = catalog_ws()
    existing = load_catalog()
    created = updated = 0
    for _, row in rows.iterrows():
        product = up(row["ProductName"])
        barcode = code(row["BarcodeOrGTIN"])
        brand = up(row["Brand"])
        product_id = "cat_" + base_db.stable_hash(barcode or product, brand, size=16)
        record = {
            "ProductId": product_id,
            "ProductName": product,
            "Brand": brand,
            "BarcodeOrGTIN": barcode,
            "Category": clean(row["Category"]),
            "NetPrice": f"{float(row['NetPrice']):.2f}",
            "VATRate": f"{float(row['VATRate']):.2f}",
            "GrossPrice": f"{float(row['GrossPrice']):.2f}",
            "UpdatedAt": datetime.now().isoformat(timespec="seconds"),
            "Source": source,
        }
        mask = existing["ProductId"].astype(str).eq(product_id)
        if mask.any():
            idx = existing.index[mask][0]
            for column in CATALOG_COLUMNS:
                existing.at[idx, column] = record[column]
            updated += 1
        else:
            existing = pd.concat([existing, pd.DataFrame([record])], ignore_index=True)
            created += 1
    ws.clear()
    ws.update("A1", [CATALOG_COLUMNS] + existing[CATALOG_COLUMNS].fillna("").astype(str).values.tolist())
    return created, updated


def make_transaction(row: pd.Series, location_id: int, transaction_id: str) -> dict[str, Any]:
    raw_data = clean(row["DataMatrixRawData"]) or clean(row["QRRawData"])
    parsed = {}
    if raw_data:
        try:
            parsed = core.parse_machine_readable_fields(raw_data)
        except Exception:
            parsed = {}
    value = code(row["BarcodeOrGTIN"])
    gtin = value if value.isdigit() and len(value) == 14 else clean(parsed.get("gtin", ""))
    barcode = "" if gtin else value
    serial = clean(row["SerialNumber"]) or clean(parsed.get("serial", "")) or clean(parsed.get("sn", ""))
    lot = clean(row["LotNumber"]) or clean(parsed.get("lot", ""))
    exp = expiry(row["ExpiryDate"]) or expiry(parsed.get("expiry", ""))
    raw_dm = clean(row["DataMatrixRawData"])
    raw_qr = clean(row["QRRawData"])
    code_value = gtin or barcode or raw_dm or raw_qr or ("CAT-" + base_db.stable_hash(row["ProductName"], row["Brand"], size=14))
    code_type = "GTIN" if gtin else ("Barcode" if barcode else ("DataMatrix" if raw_dm else ("QR" if raw_qr else "Internal")))
    qty = int(row["Quantity"])
    return core.make_transaction(
        code_type=code_type,
        code_value=code_value,
        barcode=barcode,
        gtin=gtin,
        serial_number=serial,
        lot_number=lot,
        expiry_date=exp,
        qr_raw_data=raw_qr,
        datamatrix_raw_data=raw_dm,
        brand=up(row["Brand"]),
        product=up(row["ProductName"]),
        category=clean(row["Category"]) or "Καλλυντικό",
        location_id=location_id,
        movement="Συγκεντρωτικό Excel (+)",
        quantity=qty,
        delta=qty,
        strength=up(row["Strength"]),
        dosage_form=up(row["DosageForm"]),
        note="catalog_import=true; " + clean(row["Notes"]),
        movement_kind=core.NORMAL,
        transaction_id=transaction_id,
    )


def save_stock(rows: pd.DataFrame, location_id: int, batch_id: str) -> tuple[int, int]:
    ws = core.worksheet()
    headers, _ = core.initialize_schema(ws)
    data, _ = core.load_data_cached(ws, force=True)
    existing_ids = set(data["TransactionId"].astype(str)) if not data.empty else set()
    payload = []
    skipped = 0
    for position, (_, row) in enumerate(rows.iterrows(), start=1):
        qty = int(row["Quantity"])
        if qty <= 0:
            continue
        if clean(row["SerialNumber"]) and qty != 1:
            raise ValueError(f"Το {row['ProductName']} έχει SN. Η συγκεκριμένη συσκευασία πρέπει να έχει Quantity=1.")
        transaction_id = import_io.transaction_id(batch_id, position)
        if transaction_id in existing_ids:
            skipped += 1
            continue
        transaction = make_transaction(row, location_id, transaction_id)
        payload.append([transaction.get(header, "") for header in headers])
    if payload:
        ws.append_rows(payload, value_input_option="RAW")
    return len(payload), skipped


def active_stock() -> pd.DataFrame:
    data, _ = core.load_data_cached(core.worksheet(), force=True)
    if data.empty:
        return pd.DataFrame()
    frame = data[~data["Voided"].astype(str).str.lower().isin(TRUE_VALUES)].copy()
    frame["DeltaQty"] = pd.to_numeric(frame["DeltaQty"], errors="coerce").fillna(0).astype(int)
    frame["Κωδικός"] = frame.apply(lambda row: clean(row.get("GTIN", "")) or clean(row.get("Barcode", "")), axis=1)
    frame["ExpiryDate"] = frame["ExpiryDate"].map(expiry)
    return frame


def expiry_table() -> pd.DataFrame:
    frame = active_stock()
    if frame.empty:
        return frame
    frame = frame[frame["ExpiryDate"].ne("")].copy()
    grouped = frame.groupby(["Προϊόν", "Μάρκα", "Κωδικός", "LotNumber", "ExpiryDate", "Τοποθεσία"], dropna=False, as_index=False)["DeltaQty"].sum().rename(columns={"DeltaQty": "Stock"})
    grouped = grouped[grouped["Stock"] > 0].copy()
    parsed = pd.to_datetime(grouped["ExpiryDate"], errors="coerce")
    grouped = grouped[parsed.notna()].copy()
    parsed = pd.to_datetime(grouped["ExpiryDate"], errors="coerce")
    grouped["Μέρες"] = (parsed - pd.Timestamp(date.today())).dt.days
    grouped["Ειδοποίηση"] = grouped["Μέρες"].map(lambda days: "ΛΗΓΜΕΝΟ" if days < 0 else ("≤ 6 μήνες" if days <= 183 else ("≤ 12 μήνες" if days <= 365 else "> 12 μήνες")))
    return grouped.sort_values("Μέρες")


st.set_page_config(page_title="Συγκεντρωτικά & λήξεις", page_icon="📦", layout="wide")
st.title("📦 Συγκεντρωτικά, τιμές και λήξεις")
st.caption("Νέα ροή: φωτογραφία στο ChatGPT → Excel → έλεγχος εδώ → κατάλογος και stock. Το OCR μπορεί επιτέλους να σταματήσει να παριστάνει τον λογιστή.")

import_tab, catalog_tab, expiry_tab = st.tabs(["📥 Εισαγωγή Excel", "🧾 Κατάλογος", "⏰ Λήξεις"])

with import_tab:
    st.info("Ζήτα από το ChatGPT: ProductName, Brand, BarcodeOrGTIN, Quantity και τιμή. Για φάρμακα πρόσθεσε GTIN, LOT, EXP, SN και raw QR/DataMatrix όταν είναι διαθέσιμο.")
    c1, c2 = st.columns(2)
    vat_default = c1.number_input("Προεπιλεγμένος ΦΠΑ %", 0.0, 100.0, 24.0, 1.0)
    price_mode = c2.selectbox("Αν η στήλη λέγεται απλώς Τιμή/Price", ["Τελική με ΦΠΑ", "Καθαρή χωρίς ΦΠΑ"])
    uploaded = st.file_uploader("Φόρτωσε XLSX ή CSV", type=["xlsx", "csv"])
    if uploaded:
        try:
            raw = uploaded.getvalue()
            sheets = read_file(raw, uploaded.name)
            sheet_name = st.selectbox("Φύλλο", list(sheets)) if len(sheets) > 1 else list(sheets)[0]
            draft = normalize(sheets[sheet_name], vat_default, price_mode.startswith("Τελική"))
            edited = st.data_editor(
                draft,
                hide_index=True,
                width="stretch",
                num_rows="dynamic",
                column_config={
                    "confirm": st.column_config.CheckboxColumn("OK", default=True),
                    "NetPrice": st.column_config.NumberColumn("Καθαρή", format="%.2f €"),
                    "VATRate": st.column_config.NumberColumn("ΦΠΑ %", format="%.2f"),
                    "GrossPrice": st.column_config.NumberColumn("Τελική", format="%.2f €"),
                    "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=0, step=1),
                },
            )
            chosen = edited[edited["confirm"] == True].copy()
            c1, c2 = st.columns(2)
            import_stock = c1.checkbox("Πέρασε και την ποσότητα στο stock", True)
            location = c2.selectbox("Τοποθεσία", [f"{idx} - {name}" for idx, name in LOCATIONS.items()])
            if st.button("💾 Αποθήκευση", type="primary", width="stretch"):
                if chosen.empty:
                    st.error("Δεν έχεις επιβεβαιώσει γραμμές.")
                else:
                    created, updated = save_catalog(chosen, f"{uploaded.name}:{sheet_name}")
                    added = skipped = 0
                    if import_stock:
                        batch_id = import_io.import_batch_id(raw + sheet_name.encode())
                        location_id = int(location.split("-", 1)[0])
                        added, skipped = save_stock(chosen, location_id, batch_id)
                    st.success(f"Κατάλογος: {created} νέα, {updated} ενημερωμένα. Stock: {added} κινήσεις, {skipped} διπλότυπα αγνοήθηκαν.")
                    st.rerun()
        except Exception as exc:
            st.error(f"Αποτυχία εισαγωγής: {exc}")

with catalog_tab:
    try:
        catalog = load_catalog()
        stock = active_stock()
        if catalog.empty:
            st.info("Δεν υπάρχει ακόμη κατάλογος.")
        else:
            if not stock.empty:
                summary = stock.groupby(["Προϊόν", "Κωδικός"], dropna=False, as_index=False)["DeltaQty"].sum().rename(columns={"DeltaQty": "Stock"})
                catalog["ProductName"] = catalog["ProductName"].map(up)
                catalog = catalog.merge(summary, left_on=["ProductName", "BarcodeOrGTIN"], right_on=["Προϊόν", "Κωδικός"], how="left")
                catalog["Stock"] = pd.to_numeric(catalog["Stock"], errors="coerce").fillna(0).astype(int)
            query = st.text_input("Αναζήτηση", placeholder="LIERAC, barcode, προϊόν...")
            if clean(query):
                mask = catalog.astype(str).apply(lambda column: column.str.contains(clean(query), case=False, regex=False, na=False)).any(axis=1)
                catalog = catalog[mask]
            columns = [column for column in ["ProductName", "Brand", "BarcodeOrGTIN", "Category", "NetPrice", "VATRate", "GrossPrice", "Stock", "Source", "UpdatedAt"] if column in catalog.columns]
            st.dataframe(catalog[columns], hide_index=True, width="stretch")
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε ο κατάλογος: {exc}")

with expiry_tab:
    st.caption("Οι ζώνες 12 και 6 μηνών υπολογίζονται από το ενεργό stock. Push/email χωρίς να ανοίξεις την εφαρμογή θέλει ξεχωριστό scheduler.")
    try:
        expiring = expiry_table()
        if expiring.empty:
            st.info("Δεν υπάρχουν ενεργές παρτίδες με λήξη.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("≤ 12 μήνες", int((expiring["Μέρες"] <= 365).sum()))
            c2.metric("≤ 6 μήνες", int((expiring["Μέρες"] <= 183).sum()))
            c3.metric("Ληγμένα", int((expiring["Μέρες"] < 0).sum()))
            view = st.selectbox("Προβολή", ["Όλα", "≤ 12 μήνες", "≤ 6 μήνες", "Ληγμένα"])
            shown = expiring.copy()
            if view == "≤ 12 μήνες":
                shown = shown[shown["Μέρες"] <= 365]
            elif view == "≤ 6 μήνες":
                shown = shown[shown["Μέρες"] <= 183]
            elif view == "Ληγμένα":
                shown = shown[shown["Μέρες"] < 0]
            st.dataframe(shown, hide_index=True, width="stretch")
    except Exception as exc:
        st.error(f"Δεν φορτώθηκαν οι λήξεις: {exc}")