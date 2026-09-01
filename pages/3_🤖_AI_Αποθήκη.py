import hashlib
from datetime import datetime

import pandas as pd
import streamlit as st

import ai_inventory
import app_inventory_search as core
import inventory_base as base_db


LOCATIONS = {0: "Αποθήκη", 1: "Κάτω / Κύριο Κτήριο", 2: "Πάνω / Επίπεδο 1"}
CATALOG_COLUMNS = [
    "ProductId", "ProductName", "Brand", "BarcodeOrGTIN", "Category",
    "NetPrice", "VATRate", "GrossPrice", "UpdatedAt", "Source",
]
AI_COLUMNS = [
    "confirm", "ProductName", "Brand", "BarcodeOrGTIN", "Category",
    "NetPrice", "VATRate", "GrossPrice", "Quantity", "ExpiryDate",
    "LotNumber", "SerialNumber", "GTIN", "QRRawData", "DataMatrixRawData",
    "Strength", "DosageForm", "Confidence", "Notes",
]


def clean(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def up(value):
    return " ".join(clean(value).upper().split())


def numeric(value, default=0.0):
    try:
        if value is None or clean(value) == "":
            return default
        return float(value)
    except Exception:
        return default


def normalize_ai_items(items: list[dict], default_vat: float) -> pd.DataFrame:
    rows = []
    for item in items:
        row = dict(item or {})
        barcode = clean(row.get("BarcodeOrGTIN")) or clean(row.get("GTIN"))
        vat = numeric(row.get("VATRate"), default_vat)
        if 0 < vat <= 1:
            vat *= 100
        net = numeric(row.get("NetPrice"), 0.0)
        gross = numeric(row.get("GrossPrice"), 0.0)
        if net <= 0 and gross > 0:
            net = gross / (1 + vat / 100)
        if gross <= 0 and net > 0:
            gross = net * (1 + vat / 100)
        qty = row.get("Quantity")
        try:
            qty = int(qty) if qty is not None else 0
        except Exception:
            qty = 0
        serial = clean(row.get("SerialNumber"))
        if serial and qty == 0:
            qty = 1
        rows.append({
            "confirm": False,
            "ProductName": up(row.get("ProductName")),
            "Brand": up(row.get("Brand")),
            "BarcodeOrGTIN": barcode,
            "Category": clean(row.get("Category")) or "Φάρμακο",
            "NetPrice": round(net, 2),
            "VATRate": round(vat, 2),
            "GrossPrice": round(gross, 2),
            "Quantity": max(0, qty),
            "ExpiryDate": clean(row.get("ExpiryDate")),
            "LotNumber": clean(row.get("LotNumber")),
            "SerialNumber": serial,
            "GTIN": clean(row.get("GTIN")),
            "QRRawData": clean(row.get("QRRawData")),
            "DataMatrixRawData": clean(row.get("DataMatrixRawData")),
            "Strength": up(row.get("Strength")),
            "DosageForm": up(row.get("DosageForm")),
            "Confidence": clean(row.get("Confidence")) or "low",
            "Notes": clean(row.get("Notes")),
        })
    return pd.DataFrame(rows, columns=AI_COLUMNS)


def catalog_ws():
    return base_db.ensure_worksheet(core, "Catalog", CATALOG_COLUMNS)


def catalog_product_id(row: pd.Series) -> str:
    barcode = clean(row.get("BarcodeOrGTIN")) or clean(row.get("GTIN"))
    if barcode:
        return "cat_" + base_db.stable_hash("barcode", barcode, size=16)
    return "cat_" + base_db.stable_hash("name", up(row.get("ProductName")), up(row.get("Brand")), up(row.get("Strength")), size=16)


def save_catalog_rows(rows: pd.DataFrame, source: str) -> tuple[int, int]:
    ws = catalog_ws()
    records = pd.DataFrame(ws.get_all_records())
    for column in CATALOG_COLUMNS:
        if column not in records.columns:
            records[column] = ""
    created = 0
    updated = 0
    for _, row in rows.iterrows():
        product_id = catalog_product_id(row)
        record = {
            "ProductId": product_id,
            "ProductName": up(row.get("ProductName")),
            "Brand": up(row.get("Brand")),
            "BarcodeOrGTIN": clean(row.get("BarcodeOrGTIN")) or clean(row.get("GTIN")),
            "Category": clean(row.get("Category")),
            "NetPrice": f"{numeric(row.get('NetPrice')):.2f}",
            "VATRate": f"{numeric(row.get('VATRate')):.2f}",
            "GrossPrice": f"{numeric(row.get('GrossPrice')):.2f}",
            "UpdatedAt": datetime.now().isoformat(timespec="seconds"),
            "Source": source,
        }
        mask = records["ProductId"].astype(str).eq(product_id) if not records.empty else pd.Series(dtype=bool)
        if not records.empty and mask.any():
            index = int(records.index[mask][0])
            row_number = index + 2
            ws.update(
                f"A{row_number}:J{row_number}",
                [[record.get(column, "") for column in CATALOG_COLUMNS]],
            )
            for column in CATALOG_COLUMNS:
                records.at[index, column] = record[column]
            updated += 1
        else:
            ws.append_row([record.get(column, "") for column in CATALOG_COLUMNS], value_input_option="RAW")
            records = pd.concat([records, pd.DataFrame([record])], ignore_index=True)
            created += 1
    return created, updated


def make_stock_transaction(row: pd.Series, location_id: int, transaction_id: str) -> dict:
    raw_dm = clean(row.get("DataMatrixRawData"))
    raw_qr = clean(row.get("QRRawData"))
    raw = raw_dm or raw_qr
    parsed = {}
    if raw:
        try:
            parsed = core.parse_machine_readable_fields(raw)
        except Exception:
            parsed = {}

    gtin = clean(row.get("GTIN")) or clean(parsed.get("gtin"))
    barcode_or_gtin = clean(row.get("BarcodeOrGTIN"))
    if not gtin and barcode_or_gtin.isdigit() and len(barcode_or_gtin) == 14:
        gtin = barcode_or_gtin
    barcode = "" if gtin else barcode_or_gtin
    serial = clean(row.get("SerialNumber")) or clean(parsed.get("serial")) or clean(parsed.get("sn"))
    lot = clean(row.get("LotNumber")) or clean(parsed.get("lot"))
    expiry = clean(row.get("ExpiryDate")) or clean(parsed.get("expiry"))
    quantity = int(row.get("Quantity", 0))

    code_value = gtin or barcode or raw_dm or raw_qr or ("AI-" + base_db.stable_hash(row.get("ProductName"), row.get("Brand"), size=14))
    code_type = "GTIN" if gtin else ("Barcode" if barcode else ("DataMatrix" if raw_dm else ("QR" if raw_qr else "Internal")))

    return core.make_transaction(
        code_type=code_type,
        code_value=code_value,
        barcode=barcode,
        gtin=gtin,
        serial_number=serial,
        lot_number=lot,
        expiry_date=expiry,
        qr_raw_data=raw_qr,
        datamatrix_raw_data=raw_dm,
        brand=up(row.get("Brand")),
        product=up(row.get("ProductName")),
        category=clean(row.get("Category")) or "Φάρμακο",
        location_id=location_id,
        movement="AI επιβεβαιωμένη εισαγωγή (+)",
        quantity=quantity,
        delta=quantity,
        strength=up(row.get("Strength")),
        dosage_form=up(row.get("DosageForm")),
        note="ai_confirmed=true; confidence=" + clean(row.get("Confidence")) + "; " + clean(row.get("Notes")),
        movement_kind=core.NORMAL,
        transaction_id=transaction_id,
    )


def save_stock_rows(rows: pd.DataFrame, location_id: int, batch_seed: str) -> tuple[int, int]:
    ws = core.worksheet()
    headers, _ = core.initialize_schema(ws)
    existing, _ = core.load_data_cached(ws, ttl_seconds=0)
    existing_ids = set(existing["TransactionId"].astype(str)) if not existing.empty else set()
    payload = []
    transactions = []
    skipped = 0
    for position, (_, row) in enumerate(rows.iterrows(), start=1):
        qty = int(row.get("Quantity", 0))
        if qty <= 0:
            continue
        if clean(row.get("SerialNumber")) and qty != 1:
            raise ValueError(f"Το {row.get('ProductName')} έχει SN, άρα η ποσότητα της γραμμής πρέπει να είναι 1.")
        transaction_id = f"ai-{batch_seed}-{position:04d}"
        if transaction_id in existing_ids:
            skipped += 1
            continue
        transaction = make_stock_transaction(row, location_id, transaction_id)
        transactions.append(transaction)
        payload.append([transaction.get(header, "") for header in headers])
    if payload:
        ws.append_rows(payload, value_input_option="RAW")
        for transaction in transactions:
            try:
                base_db.upsert_product_from_transaction(core, transaction)
            except Exception:
                pass
    return len(payload), skipped


st.set_page_config(page_title="AI Αποθήκη", page_icon="🤖", layout="wide")
st.title("🤖 AI Αποθήκη")
st.caption("Ανάλυση εικόνας με OpenAI → πίνακας ελέγχου → δική σου επιβεβαίωση → αποθήκευση. Το AI προτείνει, δεν κάνει μόνο του απογραφή σαν υπερενθουσιώδης πρακτικάριος.")

if "OPENAI_API_KEY" not in st.secrets:
    st.error("Λείπει το OPENAI_API_KEY από τα Streamlit Secrets. Χωρίς κλειδί η AI ανάλυση δεν μπορεί να τρέξει.")
    st.stop()

mode = st.selectbox(
    "Τύπος ανάλυσης",
    ["Φάρμακο / DataMatrix", "Συγκεντρωτικό / τιμοκατάλογος", "Ράφι / ποσότητες"],
)
col1, col2 = st.columns(2)
default_vat = col1.number_input("Προεπιλεγμένος ΦΠΑ %", min_value=0.0, max_value=100.0, value=24.0, step=1.0)
model = col2.text_input("OpenAI model", value=clean(st.secrets.get("OPENAI_MODEL", "gpt-5.6-terra")))

uploads = st.file_uploader(
    "Φωτογραφίες",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
)
if uploads:
    st.image(uploads, width=220)

if st.button("✨ Ανάλυση με AI", type="primary", disabled=not uploads, width="stretch"):
    try:
        images = [
            {"bytes": file.getvalue(), "name": file.name, "type": getattr(file, "type", "")}
            for file in uploads
        ]
        with st.spinner("Αναλύω τις εικόνες..."):
            result = ai_inventory.analyze_images(
                images,
                api_key=st.secrets["OPENAI_API_KEY"],
                mode=mode,
                default_vat=default_vat,
                model=model,
            )
        st.session_state["ai_inventory_result"] = normalize_ai_items(result.get("items", []), default_vat)
        st.session_state["ai_inventory_warnings"] = result.get("warnings", [])
        digest = hashlib.sha256(b"".join(file.getvalue() for file in uploads)).hexdigest()[:16]
        st.session_state["ai_inventory_batch"] = digest
    except Exception as exc:
        st.error(f"Αποτυχία AI ανάλυσης: {exc}")

warnings = st.session_state.get("ai_inventory_warnings", [])
if warnings:
    for warning in warnings:
        st.warning(clean(warning))

result_df = st.session_state.get("ai_inventory_result")
if isinstance(result_df, pd.DataFrame) and not result_df.empty:
    st.subheader("Έλεγχος αποτελέσματος")
    st.info("Τσέκαρε OK μόνο στις σωστές γραμμές. Μπορείς να διορθώσεις όλα τα πεδία πριν γίνει αποθήκευση.")
    edited = st.data_editor(
        result_df,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "confirm": st.column_config.CheckboxColumn("OK", default=False),
            "NetPrice": st.column_config.NumberColumn("Καθαρή", min_value=0.0, format="%.2f €"),
            "VATRate": st.column_config.NumberColumn("ΦΠΑ %", min_value=0.0, max_value=100.0, format="%.2f"),
            "GrossPrice": st.column_config.NumberColumn("Τελική", min_value=0.0, format="%.2f €"),
            "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=0, step=1),
            "Confidence": st.column_config.SelectboxColumn("Βεβαιότητα", options=["high", "medium", "low"]),
        },
        key="ai_inventory_editor",
    )
    st.session_state["ai_inventory_result"] = edited
    chosen = edited[edited["confirm"] == True].copy()

    c1, c2, c3 = st.columns(3)
    save_catalog = c1.checkbox("Αποθήκευση στον κατάλογο", value=True)
    save_stock = c2.checkbox("Πέρασμα ποσότητας στο stock", value=True)
    location_label = c3.selectbox("Τοποθεσία", [f"{idx} - {name}" for idx, name in LOCATIONS.items()])

    if st.button("💾 Αποθήκευση επιβεβαιωμένων", type="primary", width="stretch"):
        try:
            if chosen.empty:
                raise ValueError("Δεν έχεις επιβεβαιώσει καμία γραμμή.")
            if chosen["ProductName"].map(clean).eq("").any():
                raise ValueError("Κάθε επιβεβαιωμένη γραμμή χρειάζεται όνομα προϊόντος.")
            created = updated = added = skipped = 0
            source = "OpenAI image analysis " + datetime.now().isoformat(timespec="seconds")
            if save_catalog:
                created, updated = save_catalog_rows(chosen, source)
            if save_stock:
                location_id = int(location_label.split("-", 1)[0].strip())
                batch_seed = clean(st.session_state.get("ai_inventory_batch")) or hashlib.sha256(source.encode()).hexdigest()[:16]
                added, skipped = save_stock_rows(chosen, location_id, batch_seed)
            st.success(
                f"Ολοκληρώθηκε. Κατάλογος: {created} νέα, {updated} ενημερωμένα. "
                f"Stock: {added} νέες κινήσεις, {skipped} διπλότυπα αγνοήθηκαν."
            )
        except Exception as exc:
            st.error(f"Δεν αποθηκεύτηκαν οι γραμμές: {exc}")
elif result_df is not None:
    st.info("Η ανάλυση δεν επέστρεψε προϊόντα.")
