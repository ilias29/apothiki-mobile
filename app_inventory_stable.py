import calendar
import hashlib
import uuid
from datetime import date, datetime
from typing import Any

import pandas as pd
import streamlit as st

import ai_inventory
import app_inventory_search as core
import inventory_base as base_db
import inventory_csa as csa


CATEGORIES = ["Φάρμακο", "Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Ορθοπεδικό", "Βρεφικό", "Άλλο"]
LOCATIONS = {0: "Αποθήκη", 1: "Κάτω / Κύριο Κτήριο", 2: "Πάνω / Επίπεδο 1"}
DEFAULT_VAT = 24.0


def clean(value: Any) -> str:
    return csa.clean(value)


def fresh_data() -> pd.DataFrame:
    ws = core.worksheet()
    core.initialize_schema(ws)
    data, _ = core.load_data_cached(ws, ttl_seconds=0)
    return data


def append_transactions(transactions: list[dict]) -> tuple[int, int]:
    ws = core.worksheet()
    saved = 0
    duplicate = 0
    for transaction in transactions:
        status = core.append_stock_transaction(ws, transaction)
        if status == "duplicate":
            duplicate += 1
        else:
            saved += 1
        try:
            base_db.upsert_product_from_transaction(core, transaction)
        except Exception:
            pass
    core.invalidate_data_cache(ws)
    return saved, duplicate


def images_from_uploads(files) -> list[dict[str, Any]]:
    return [
        {
            "bytes": file.getvalue(),
            "name": file.name,
            "type": getattr(file, "type", "image/jpeg"),
        }
        for file in (files or [])
    ]


def normalize_expiry(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    for sep in ("/", "-", "."):
        parts = text.split(sep)
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            month, year = int(parts[0]), int(parts[1])
            if 1 <= month <= 12 and 2000 <= year <= 2200:
                last_day = calendar.monthrange(year, month)[1]
                return date(year, month, last_day).isoformat()
    parsed = pd.to_datetime(text, errors="coerce")
    return parsed.date().isoformat() if not pd.isna(parsed) else text


def apply_pending_document_metadata() -> None:
    pending = st.session_state.pop("main_pending_document", None)
    if not isinstance(pending, dict):
        return
    supplier = clean(pending.get("Supplier"))
    document_number = clean(pending.get("DocumentNumber"))
    document_date = clean(pending.get("DocumentDate"))
    if supplier:
        st.session_state["main_supplier"] = supplier
    if document_number:
        st.session_state["main_reference"] = document_number
    if document_date:
        parsed = pd.to_datetime(document_date, errors="coerce")
        if not pd.isna(parsed):
            st.session_state["main_document_date"] = parsed.date()


def stock_label(row: pd.Series) -> str:
    code = clean(row.get("GTIN")) or clean(row.get("Barcode")) or clean(row.get("CodeValue"))
    return (
        f"{clean(row.get('Προϊόν'))} | {clean(row.get('Strength')) or '-'} | "
        f"stock {int(row.get('Stock', 0))} | {clean(row.get('Τοποθεσία'))} | {code}"
    )


def lot_label(row: pd.Series) -> str:
    code = clean(row.get("GTIN")) or clean(row.get("Barcode")) or clean(row.get("CodeValue"))
    return (
        f"{clean(row.get('Προϊόν'))} | stock {int(row.get('Stock', 0))} | "
        f"λήξη {clean(row.get('ExpiryDate')) or '-'} | LOT {clean(row.get('LotNumber')) or '-'} | "
        f"{clean(row.get('Τοποθεσία'))} | {code}"
    )


def manual_entry_tab() -> None:
    st.subheader("➕ Χειροκίνητη καταχώρηση")
    st.caption("Για ένα προϊόν τη φορά. Η AI παραλαβή είναι για τιμολόγια και οθόνες, εδώ κρατάμε το απλό χειροκίνητο fallback.")

    c1, c2 = st.columns(2)
    code = c1.text_input("Barcode / GTIN", key="manual_code")
    product = c2.text_input("Προϊόν", key="manual_product")
    c3, c4 = st.columns(2)
    brand = c3.text_input("Μάρκα / Εταιρεία", key="manual_brand")
    category = c4.selectbox("Κατηγορία", CATEGORIES, key="manual_category")
    c5, c6 = st.columns(2)
    strength = c5.text_input("Περιεκτικότητα", key="manual_strength")
    dosage_form = c6.text_input("Μορφή", key="manual_form")
    c7, c8 = st.columns(2)
    expiry = c7.text_input("Λήξη", placeholder="YYYY-MM-DD ή MM/YYYY", key="manual_expiry")
    lot = c8.text_input("LOT / Παρτίδα", key="manual_lot")
    c9, c10 = st.columns(2)
    serial = c9.text_input("Serial Number (αν υπάρχει)", key="manual_serial")
    qty = c10.number_input("Ποσότητα", min_value=1, value=1, step=1, key="manual_qty")
    location_label = st.selectbox(
        "Τοποθεσία",
        [f"{idx} - {name}" for idx, name in LOCATIONS.items()],
        index=2,
        key="manual_location",
    )
    no_expiry = st.checkbox("Δεν υπάρχει / δεν είναι διαθέσιμη λήξη", key="manual_no_expiry")
    confirm = st.checkbox("Επιβεβαιώνω τα στοιχεία", key="manual_confirm")

    if st.button("💾 Αποθήκευση στο stock", type="primary", width="stretch", key="manual_save"):
        try:
            if not clean(product):
                raise core.InventoryError("Βάλε όνομα προϊόντος.")
            if not confirm:
                raise core.InventoryError("Χρειάζεται επιβεβαίωση πριν την αποθήκευση.")
            if clean(serial) and int(qty) != 1:
                raise core.InventoryError("Όταν υπάρχει Serial Number, η ποσότητα της γραμμής πρέπει να είναι 1.")
            expiry_value = normalize_expiry(expiry)
            if not expiry_value and not no_expiry:
                raise core.InventoryError("Βάλε λήξη ή επίλεξε ότι δεν είναι διαθέσιμη.")

            raw_code = clean(code)
            if raw_code:
                if raw_code.isdigit() and len(raw_code) == 14:
                    code_type, code_value, barcode, gtin = "GTIN", raw_code, "", raw_code
                else:
                    code_type, code_value, barcode, gtin = "Barcode", raw_code, raw_code, ""
            else:
                internal = csa.internal_identity(product, brand, strength)
                code_type, code_value, barcode, gtin = "Internal", internal, "", ""

            location_id = int(location_label.split("-", 1)[0].strip())
            transaction = core.make_transaction(
                code_type=code_type,
                code_value=code_value,
                barcode=barcode,
                gtin=gtin,
                serial_number=clean(serial),
                lot_number=clean(lot),
                expiry_date=expiry_value,
                strength=clean(strength),
                dosage_form=clean(dosage_form),
                brand=clean(brand),
                product=clean(product),
                category=clean(category),
                location_id=location_id,
                movement="Χειροκίνητη παραλαβή (+)",
                quantity=int(qty),
                delta=int(qty),
                note="source=manual_entry",
                movement_kind=core.NORMAL,
            )
            saved, _ = append_transactions([transaction])
            st.success(f"Αποθηκεύτηκε {saved} κίνηση.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def ai_receipt_tab(api_key: str, model: str) -> None:
    apply_pending_document_metadata()
    st.subheader("📥 AI Παραλαβή")
    st.caption("Φωτογραφία τιμολογίου ή οθόνης → AI → έλεγχος → stock. Προτεραιότητα σε ποσότητα, ημερομηνία, LOT και λήξη, όχι σε τιμές.")

    if not api_key:
        st.error("Λείπει το OPENAI_API_KEY από τα Streamlit Secrets. Οι υπόλοιπες λειτουργίες της αποθήκης συνεχίζουν να δουλεύουν.")
        return

    a, b, c = st.columns(3)
    supplier = a.text_input("Προμηθευτής", key="main_supplier")
    reference = b.text_input("Αρ. τιμολογίου / αναφορά", key="main_reference")
    document_date = c.date_input("Ημερομηνία παραστατικού", value=date.today(), key="main_document_date")
    location_label = st.selectbox(
        "Παραλαβή σε",
        [f"{idx} - {name}" for idx, name in LOCATIONS.items()],
        index=2,
        key="main_receipt_location",
    )

    uploads = st.file_uploader(
        "Φωτογραφίες / screenshots τιμολογίου",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="main_receipt_uploads",
        help="Στο κινητό μπορείς να επιλέξεις Κάμερα ή Gallery από το πεδίο αρχείων.",
    )
    if uploads:
        st.image(uploads, width=180)

    if st.button("✨ Ανάλυση τιμολογίου με AI", type="primary", disabled=not uploads, width="stretch", key="main_receipt_analyze"):
        try:
            images = images_from_uploads(uploads)
            with st.spinner("Διαβάζω ημερομηνία, προϊόντα, ποσότητες, LOT και λήξεις..."):
                result = ai_inventory.analyze_images(
                    images,
                    api_key=api_key,
                    mode="Τιμολόγιο / παραλαβή",
                    default_vat=DEFAULT_VAT,
                    model=model,
                )
            st.session_state["main_receipt_rows"] = csa.normalize_ai_items(result.get("items", []), DEFAULT_VAT)
            st.session_state["main_receipt_warnings"] = result.get("warnings", [])
            seed_reference = clean(reference) or clean(result.get("document", {}).get("DocumentNumber"))
            st.session_state["main_receipt_seed"] = csa.batch_seed(
                [image["bytes"] for image in images],
                seed_reference,
            )
            st.session_state["main_pending_document"] = result.get("document", {})
            st.rerun()
        except Exception as exc:
            st.error(f"Αποτυχία ανάλυσης: {exc}")

    for warning in st.session_state.get("main_receipt_warnings", []):
        st.warning(clean(warning))

    rows = st.session_state.get("main_receipt_rows")
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return

    missing_expiry = rows["ExpiryDate"].astype(str).str.strip().eq("").sum()
    missing_lot = rows["LotNumber"].astype(str).str.strip().eq("").sum()
    m1, m2, m3 = st.columns(3)
    m1.metric("Γραμμές", len(rows))
    m2.metric("Χωρίς λήξη", int(missing_expiry))
    m3.metric("Χωρίς LOT", int(missing_lot))

    receipt_columns = [
        "RowId", "confirm", "ProductName", "Quantity", "ExpiryDate", "LotNumber",
        "BarcodeOrGTIN", "GTIN", "Strength", "Brand", "Category", "DosageForm",
        "SerialNumber", "Confidence", "Notes",
    ]
    edited = st.data_editor(
        rows[receipt_columns],
        hide_index=True,
        width="stretch",
        disabled=["RowId", "Confidence"],
        column_config={
            "RowId": st.column_config.NumberColumn("#"),
            "confirm": st.column_config.CheckboxColumn("OK", default=False),
            "ProductName": st.column_config.TextColumn("Προϊόν"),
            "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=0, step=1),
            "ExpiryDate": st.column_config.TextColumn("Λήξη"),
            "LotNumber": st.column_config.TextColumn("LOT"),
            "BarcodeOrGTIN": st.column_config.TextColumn("Barcode / GTIN"),
            "Strength": st.column_config.TextColumn("Περιεκτικότητα"),
            "Brand": st.column_config.TextColumn("Μάρκα"),
            "SerialNumber": st.column_config.TextColumn("SN"),
        },
        key="main_receipt_editor",
    )
    chosen = edited[edited["confirm"] == True].copy()
    st.caption("Τσέκαρε OK μόνο στις σωστές γραμμές. Αν LOT ή λήξη δεν φαίνονται, μένουν κενά. Δεν τα μαντεύουμε για να νιώθει παραγωγικό το μοντέλο.")

    if st.button("💾 Επιβεβαίωση και προσθήκη στο stock", type="primary", disabled=chosen.empty, width="stretch", key="main_receipt_save"):
        try:
            location_id = int(location_label.split("-", 1)[0].strip())
            seed = clean(st.session_state.get("main_receipt_seed"))
            note = csa.source_note(
                supplier=supplier,
                reference=reference,
                document_date=document_date.isoformat(),
            )
            transactions = []
            for _, row in chosen.iterrows():
                row_id = int(row["RowId"])
                row = row.copy()
                if clean(row.get("ExpiryDate")):
                    row["ExpiryDate"] = normalize_expiry(row.get("ExpiryDate"))
                transactions.append(csa.receipt_transaction(
                    row,
                    location_id=location_id,
                    transaction_id=f"main-ai-in-{seed}-{row_id:04d}",
                    source_note=note,
                ))
            saved, duplicate = append_transactions(transactions)
            st.success(f"Παραλαβή: {saved} κινήσεις αποθηκεύτηκαν, {duplicate} διπλότυπες αγνοήθηκαν.")
            for key in ["main_receipt_rows", "main_receipt_warnings", "main_receipt_seed", "main_receipt_editor"]:
                st.session_state.pop(key, None)
            st.rerun()
        except Exception as exc:
            st.error(f"Δεν αποθηκεύτηκε η παραλαβή: {exc}")


def issue_tab(api_key: str, model: str) -> None:
    st.subheader("📤 Έξοδος / τι έφυγε")
    st.caption("Ό,τι δόθηκε, χρησιμοποιήθηκε ή πέρασε αλλού αφαιρείται από το stock. Η αφαίρεση γίνεται FEFO, πρώτα από την παρτίδα που λήγει νωρίτερα.")

    try:
        snapshot = csa.stock_snapshot(fresh_data())
        summary = csa.product_summary(snapshot)
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε το stock: {exc}")
        return

    if summary.empty:
        st.info("Δεν υπάρχει διαθέσιμο stock για έξοδο.")
        return

    mode = st.radio(
        "Τρόπος εξόδου",
        ["Χειροκίνητα", "AI από φωτογραφία / screenshot"],
        horizontal=True,
        key="main_issue_mode",
    )

    if mode == "Χειροκίνητα":
        query = st.text_input("Προϊόν, μάρκα ή barcode", key="main_issue_query")
        matches = csa.filter_summary(summary, query)
        if clean(query) and matches.empty:
            st.warning("Δεν βρέθηκε προϊόν.")
            return
        if matches.empty:
            st.info("Γράψε προϊόν ή barcode για να βρεις τι έφυγε.")
            return
        options = list(matches.index)
        selected_index = st.selectbox(
            "Προϊόν",
            options,
            format_func=lambda idx: stock_label(matches.loc[idx]),
            key="main_issue_product",
        )
        selected = matches.loc[selected_index]
        available = int(selected["Stock"])
        qty = st.number_input(
            "Ποσότητα που έφυγε",
            min_value=1,
            max_value=max(1, available),
            value=1,
            step=1,
            key="main_issue_qty",
        )
        reason = st.selectbox(
            "Αιτία",
            ["Πώληση / χορήγηση", "Χρήση / κατανάλωση", "Μεταφορά", "Διόρθωση αποθέματος"],
            key="main_issue_reason",
        )
        note = st.text_input("Σημείωση", key="main_issue_note")
        if st.button("➖ Αφαίρεση από stock", type="primary", width="stretch", key="main_issue_save"):
            try:
                prefix = "main-out-" + uuid.uuid4().hex[:18]
                transactions = csa.fefo_issue_transactions(
                    snapshot,
                    selected,
                    quantity=int(qty),
                    note=f"reason={reason}; {clean(note)}",
                    transaction_prefix=prefix,
                )
                saved, duplicate = append_transactions(transactions)
                st.success(f"Αφαιρέθηκαν {int(qty)} τεμάχια. Κινήσεις: {saved}, διπλότυπα: {duplicate}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Δεν έγινε η έξοδος: {exc}")
        return

    if not api_key:
        st.error("Λείπει το OPENAI_API_KEY, άρα η AI έξοδος δεν μπορεί να τρέξει.")
        return

    out_uploads = st.file_uploader(
        "Φωτογραφίες / screenshots με όσα έφυγαν",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="main_issue_uploads",
    )
    if out_uploads:
        st.image(out_uploads, width=180)

    if st.button("✨ Ανάλυση εξόδου με AI", type="primary", disabled=not out_uploads, width="stretch", key="main_issue_analyze"):
        try:
            images = images_from_uploads(out_uploads)
            with st.spinner("Διαβάζω τι έφυγε..."):
                result = ai_inventory.analyze_images(
                    images,
                    api_key=api_key,
                    mode="Έξοδος / πωλήσεις",
                    default_vat=DEFAULT_VAT,
                    model=model,
                )
            st.session_state["main_issue_rows"] = csa.normalize_ai_items(result.get("items", []), DEFAULT_VAT)
            st.session_state["main_issue_warnings"] = result.get("warnings", [])
        except Exception as exc:
            st.error(f"Αποτυχία ανάλυσης: {exc}")

    for warning in st.session_state.get("main_issue_warnings", []):
        st.warning(clean(warning))

    issue_rows = st.session_state.get("main_issue_rows")
    if not isinstance(issue_rows, pd.DataFrame) or issue_rows.empty:
        return

    issue_columns = ["RowId", "confirm", "ProductName", "Brand", "BarcodeOrGTIN", "GTIN", "Strength", "Quantity", "Confidence", "Notes"]
    edited = st.data_editor(
        issue_rows[issue_columns],
        hide_index=True,
        width="stretch",
        disabled=["RowId", "Confidence"],
        column_config={
            "confirm": st.column_config.CheckboxColumn("OK", default=False),
            "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=0, step=1),
        },
        key="main_issue_editor",
    )
    chosen = edited[edited["confirm"] == True].copy()
    if st.button("➖ Επιβεβαίωση και αφαίρεση", type="primary", disabled=chosen.empty, width="stretch", key="main_issue_ai_save"):
        try:
            transactions, errors = csa.ai_issue_transactions(
                snapshot,
                summary,
                chosen,
                note_prefix="source=MAIN_AI_OUT",
            )
            if errors:
                st.error("\n".join(errors))
            if transactions:
                saved, duplicate = append_transactions(transactions)
                st.success(f"AI έξοδος: {saved} κινήσεις αποθηκεύτηκαν, {duplicate} διπλότυπες.")
                for key in ["main_issue_rows", "main_issue_warnings", "main_issue_editor"]:
                    st.session_state.pop(key, None)
                st.rerun()
        except Exception as exc:
            st.error(f"Δεν έγινε η AI έξοδος: {exc}")


def quick_update_tab() -> None:
    st.subheader("🔄 Γρήγορη διόρθωση")
    st.caption("Για μικρές διορθώσεις συγκεκριμένης παρτίδας. Δεν αντικαθιστά την παραλαβή ή την έξοδο, απλώς γλιτώνει τη φόρμα-μαμούθ.")
    try:
        snapshot = csa.stock_snapshot(fresh_data())
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε το stock: {exc}")
        return
    if snapshot.empty:
        st.info("Δεν υπάρχει stock.")
        return

    query = st.text_input("Αναζήτηση προϊόντος / barcode / LOT", key="quick_query")
    if not clean(query):
        st.info("Γράψε κάτι για αναζήτηση.")
        return
    q = clean(query).lower()
    mask = pd.Series(False, index=snapshot.index)
    for column in ["Προϊόν", "Μάρκα", "Barcode", "GTIN", "CodeValue", "LotNumber", "Strength"]:
        mask |= snapshot[column].astype(str).str.lower().str.contains(q, regex=False, na=False)
    matches = snapshot[mask].copy()
    if matches.empty:
        st.warning("Δεν βρέθηκε παρτίδα.")
        return

    options = list(matches.index)
    selected_index = st.selectbox("Παρτίδα", options, format_func=lambda idx: lot_label(matches.loc[idx]), key="quick_lot")
    selected = matches.loc[selected_index]
    current = int(selected["Stock"])
    c1, c2, c3, c4 = st.columns(4)
    delta = None
    if c1.button("-1", key="quick_m1"):
        delta = -1
    if c2.button("-5", key="quick_m5"):
        delta = -5
    if c3.button("+1", key="quick_p1"):
        delta = 1
    if c4.button("+5", key="quick_p5"):
        delta = 5
    custom = st.number_input("Ή δική σου μεταβολή", min_value=-999, max_value=999, value=0, step=1, key="quick_custom")
    note = st.text_input("Σημείωση", key="quick_note")
    if st.button("✅ Εφαρμογή μεταβολής", key="quick_apply"):
        delta = int(custom)

    if delta is None:
        return
    if delta == 0:
        st.error("Μηδενική μεταβολή δεν αποθηκεύεται. Το σύστημα έχει ήδη αρκετά πράγματα να θυμάται.")
        return
    if current + int(delta) < 0:
        st.error(f"Η παρτίδα έχει {current} τεμάχια. Δεν μπορείς να αφαιρέσεις {abs(int(delta))}.")
        return

    try:
        transaction = core.make_transaction(
            code_type=clean(selected["CodeType"]),
            code_value=clean(selected["CodeValue"]),
            barcode=clean(selected["Barcode"]),
            gtin=clean(selected["GTIN"]),
            serial_number=clean(selected["SerialNumber"]) if abs(int(delta)) == 1 else "",
            lot_number=clean(selected["LotNumber"]),
            expiry_date=clean(selected["ExpiryDate"]),
            strength=clean(selected["Strength"]),
            dosage_form=clean(selected["DosageForm"]),
            brand=clean(selected["Μάρκα"]),
            product=clean(selected["Προϊόν"]),
            category=clean(selected["Κατηγορία"]),
            location_id=int(selected["LocationId"]),
            movement="Γρήγορη διόρθωση (+)" if int(delta) > 0 else "Γρήγορη διόρθωση (-)",
            quantity=abs(int(delta)),
            delta=int(delta),
            note=f"source=quick_adjustment; {clean(note)}",
            movement_kind=core.NORMAL,
        )
        saved, _ = append_transactions([transaction])
        st.success(f"Περάστηκε μεταβολή {int(delta):+d}. Κινήσεις: {saved}.")
        st.rerun()
    except Exception as exc:
        st.error(f"Δεν αποθηκεύτηκε: {exc}")


def stock_tab() -> None:
    st.subheader("📦 Τι υπάρχει τώρα")
    try:
        snapshot = csa.stock_snapshot(fresh_data())
        summary = csa.product_summary(snapshot)
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε το stock: {exc}")
        return
    if summary.empty:
        st.info("Δεν υπάρχει ενεργό stock.")
        return

    q = st.text_input("Αναζήτηση stock", key="stock_query")
    shown = csa.filter_summary(summary, q)
    total_units = int(pd.to_numeric(shown["Stock"], errors="coerce").fillna(0).sum()) if not shown.empty else 0
    c1, c2 = st.columns(2)
    c1.metric("Προϊόντα / τοποθεσίες", len(shown))
    c2.metric("Σύνολο τεμαχίων", total_units)
    view_cols = ["Προϊόν", "Μάρκα", "Strength", "Barcode", "GTIN", "Τοποθεσία", "Stock", "Κατηγορία"]
    st.dataframe(shown[view_cols], hide_index=True, width="stretch")

    with st.expander("Παρτίδες / LOT / λήξεις", expanded=True):
        lot_cols = ["Προϊόν", "Μάρκα", "LotNumber", "ExpiryDate", "SerialNumber", "Τοποθεσία", "Stock", "GTIN", "Barcode"]
        st.dataframe(snapshot[lot_cols], hide_index=True, width="stretch")


def expiry_tab() -> None:
    st.subheader("⚠️ Λήξεις")
    try:
        snapshot = csa.stock_snapshot(fresh_data())
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε το stock: {exc}")
        return
    if snapshot.empty:
        st.info("Δεν υπάρχει ενεργό stock.")
        return

    frame = core.add_expiry_columns(snapshot)
    expiry_dates = pd.to_datetime(frame["ExpiryDate"], errors="coerce")
    today = pd.Timestamp(date.today())
    days = (expiry_dates - today).dt.days
    frame["DaysToExpiry"] = days

    expired = frame[days < 0].copy()
    soon = frame[(days >= 0) & (days <= 90)].copy()
    later = frame[days > 90].copy()
    missing = frame[expiry_dates.isna()].copy()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Ληγμένες παρτίδες", len(expired))
    m2.metric("0–90 ημέρες", len(soon))
    m3.metric(">90 ημέρες", len(later))
    m4.metric("Χωρίς λήξη", len(missing))

    cols = ["Προϊόν", "Μάρκα", "LotNumber", "ExpiryDate", "DaysToExpiry", "Τοποθεσία", "Stock", "GTIN", "Barcode"]
    with st.expander("🔴 Ληγμένα", expanded=not expired.empty):
        st.dataframe(expired[cols], hide_index=True, width="stretch")
    with st.expander("🟠 Λήγουν μέσα σε 90 ημέρες", expanded=True):
        st.dataframe(soon[cols], hide_index=True, width="stretch")
    with st.expander("🟢 Αργότερα", expanded=False):
        st.dataframe(later[cols], hide_index=True, width="stretch")
    with st.expander("⚪ Χωρίς καταγεγραμμένη λήξη", expanded=False):
        st.dataframe(missing[["Προϊόν", "Μάρκα", "LotNumber", "Τοποθεσία", "Stock", "GTIN", "Barcode"]], hide_index=True, width="stretch")


def base_tab() -> None:
    st.subheader("🧱 Βάση προϊόντων")
    st.caption("Το stock βγαίνει από τις κινήσεις. Η βάση προϊόντων βοηθά μόνο στην ταυτοποίηση και στην οργάνωση.")
    try:
        data = fresh_data()
        inferred_products = base_db.product_rows_from_transactions(data)
        products_df = base_db.read_sheet_df(core, "Products", base_db.PRODUCT_COLUMNS)
        mappings_df = base_db.read_sheet_df(core, "SupplierMappings", base_db.SUPPLIER_MAPPING_COLUMNS)
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε η βάση: {exc}")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Προϊόντα από κινήσεις", len(inferred_products))
    m2.metric("Products sheet", len(products_df))
    m3.metric("Supplier mappings", len(mappings_df))
    c1, c2 = st.columns(2)
    if c1.button("Δημιουργία / έλεγχος φύλλων βάσης", width="stretch"):
        try:
            sizes = base_db.ensure_base_sheets(core)
            st.success("Έτοιμα: " + ", ".join(f"{name}: {count}" for name, count in sizes.items()))
        except Exception as exc:
            st.error(str(exc))
    if c2.button("Συγχρονισμός Products από κινήσεις", width="stretch"):
        try:
            result = base_db.sync_products_from_transactions(core, data)
            st.success(f"Προστέθηκαν {result['added']} νέα προϊόντα. Υπήρχαν ήδη {result['existing']}.")
        except Exception as exc:
            st.error(str(exc))
    if not products_df.empty:
        st.dataframe(products_df, hide_index=True, width="stretch")


def main() -> None:
    st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="📦", layout="wide")
    st.title("📦 Αποθήκη Φαρμακείου")
    st.caption("Ένα app: τι μπήκε → τι έφυγε → τι υπάρχει → τι λήγει. Η AI βοηθά στην ανάγνωση, αλλά δεν γράφει stock χωρίς δικό σου ΟΚ.")

    api_key = clean(st.secrets.get("OPENAI_API_KEY", ""))
    model = clean(st.secrets.get("OPENAI_MODEL", "gpt-5.6-terra"))

    tab_receipt, tab_issue, tab_stock, tab_expiry, tab_manual, tab_quick, tab_base = st.tabs([
        "📥 AI Παραλαβή",
        "📤 Έξοδος",
        "📦 Τι υπάρχει",
        "⚠️ Λήξεις",
        "➕ Καταχώρηση",
        "🔄 Διόρθωση",
        "🧱 Βάση",
    ])
    with tab_receipt:
        ai_receipt_tab(api_key, model)
    with tab_issue:
        issue_tab(api_key, model)
    with tab_stock:
        stock_tab()
    with tab_expiry:
        expiry_tab()
    with tab_manual:
        manual_entry_tab()
    with tab_quick:
        quick_update_tab()
    with tab_base:
        base_tab()


if __name__ == "__main__":
    main()
