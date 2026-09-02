import uuid
from datetime import date

import pandas as pd
import streamlit as st

import ai_inventory
import app_inventory_search as core
import inventory_csa as csa


LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}
DEFAULT_VAT = 24.0


def fresh_data() -> pd.DataFrame:
    ws = core.worksheet()
    core.initialize_schema(ws)
    data, _ = core.load_data_cached(ws, ttl_seconds=0)
    return data


def append_transactions(transactions: list[dict]) -> tuple[int, int]:
    ws = core.worksheet()
    saved = duplicate = 0
    for tx in transactions:
        status = core.append_stock_transaction(ws, tx)
        if status == "duplicate":
            duplicate += 1
        else:
            saved += 1
    return saved, duplicate


def images_from_uploads(files) -> list[dict]:
    return [
        {"bytes": file.getvalue(), "name": file.name, "type": getattr(file, "type", "image/jpeg")}
        for file in (files or [])
    ]


def stock_label(row: pd.Series) -> str:
    code = csa.clean(row.get("GTIN")) or csa.clean(row.get("Barcode")) or csa.clean(row.get("CodeValue"))
    return f"{csa.clean(row.get('Προϊόν'))} | {csa.clean(row.get('Μάρκα'))} | stock {int(row.get('Stock', 0))} | {csa.clean(row.get('Τοποθεσία'))} | {code}"


def apply_pending_document_metadata() -> None:
    pending = st.session_state.pop("csa_pending_document", None)
    if not isinstance(pending, dict):
        return
    supplier = csa.clean(pending.get("Supplier"))
    document_number = csa.clean(pending.get("DocumentNumber"))
    document_date = csa.clean(pending.get("DocumentDate"))
    if supplier:
        st.session_state["csa_supplier"] = supplier
    if document_number:
        st.session_state["csa_reference"] = document_number
    if document_date:
        parsed = pd.to_datetime(document_date, errors="coerce")
        if not pd.isna(parsed):
            st.session_state["csa_document_date"] = parsed.date()


st.set_page_config(page_title="Pharmacy CSA", page_icon="💊", layout="wide")
st.title("💊 Pharmacy CSA")
st.caption(
    "Προτεραιότητα: τι μπήκε, πόσο μπήκε, πότε μπήκε, LOT και πότε λήγει. "
    "Οι τιμές είναι δευτερεύουσες. Το AI προτείνει και εσύ επιβεβαιώνεις πριν αλλάξει το stock."
)

if "OPENAI_API_KEY" not in st.secrets:
    st.error("Λείπει το OPENAI_API_KEY από τα Streamlit Secrets.")
    st.stop()

model = csa.clean(st.secrets.get("OPENAI_MODEL", "gpt-5.6-terra"))
receipt_tab, issue_tab, stock_tab = st.tabs(["📥 AI Παραλαβή", "📤 Έξοδος", "📦 Stock & λήξεις"])

with receipt_tab:
    apply_pending_document_metadata()
    st.subheader("Τιμολόγιο ή οθόνη υπολογιστή")
    st.info(
        "Από κινητό πάτησε το πεδίο φωτογραφιών και διάλεξε Κάμερα ή Gallery. "
        "Το AI θα προσπαθήσει πρώτα να βρει ημερομηνία παραστατικού, προϊόν, ποσότητα, LOT και λήξη."
    )

    a, b, c = st.columns(3)
    supplier = a.text_input("Προμηθευτής", key="csa_supplier")
    reference = b.text_input("Αρ. τιμολογίου / αναφορά", key="csa_reference")
    document_date = c.date_input("Ημερομηνία παραστατικού", value=date.today(), key="csa_document_date")
    location_label = st.selectbox(
        "Παραλαβή σε",
        [f"{k} - {v}" for k, v in LOCATIONS.items()],
        key="csa_receipt_location",
    )

    uploads = st.file_uploader(
        "Φωτογραφίες / screenshots τιμολογίου",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="csa_receipt_uploads",
    )
    if uploads:
        st.image(uploads, width=180)

    if st.button("✨ Ανάλυση τιμολογίου με AI", type="primary", disabled=not uploads, width="stretch"):
        try:
            images = images_from_uploads(uploads)
            with st.spinner("Διαβάζω ημερομηνία, προϊόντα, ποσότητες, LOT και λήξεις..."):
                result = ai_inventory.analyze_images(
                    images,
                    api_key=st.secrets["OPENAI_API_KEY"],
                    mode="Τιμολόγιο / παραλαβή",
                    default_vat=DEFAULT_VAT,
                    model=model,
                )
            st.session_state["csa_receipt_rows"] = csa.normalize_ai_items(result.get("items", []), DEFAULT_VAT)
            st.session_state["csa_receipt_warnings"] = result.get("warnings", [])
            st.session_state["csa_receipt_seed"] = csa.batch_seed(
                [image["bytes"] for image in images],
                f"{supplier}|{reference}|{document_date.isoformat()}" if csa.clean(reference) else "",
            )
            st.session_state["csa_pending_document"] = result.get("document", {})
            st.rerun()
        except Exception as exc:
            st.error(f"Αποτυχία ανάλυσης: {exc}")

    for warning in st.session_state.get("csa_receipt_warnings", []):
        st.warning(csa.clean(warning))

    receipt_rows = st.session_state.get("csa_receipt_rows")
    if isinstance(receipt_rows, pd.DataFrame) and not receipt_rows.empty:
        missing_expiry = receipt_rows["ExpiryDate"].astype(str).str.strip().eq("").sum()
        missing_lot = receipt_rows["LotNumber"].astype(str).str.strip().eq("").sum()
        m1, m2, m3 = st.columns(3)
        m1.metric("Γραμμές", len(receipt_rows))
        m2.metric("Χωρίς λήξη", int(missing_expiry))
        m3.metric("Χωρίς LOT", int(missing_lot))

        st.caption(
            "Έλεγξε κυρίως Ποσότητα, Λήξη και LOT. Αν δεν φαίνονται στην εικόνα, άφησέ τα κενά. "
            "Δεν μαντεύουμε φαρμακευτικά δεδομένα επειδή το AI ξύπνησε δημιουργικό."
        )
        receipt_columns = [
            "RowId", "confirm", "ProductName", "Quantity", "ExpiryDate", "LotNumber",
            "BarcodeOrGTIN", "GTIN", "Strength", "Brand", "Category", "DosageForm",
            "SerialNumber", "Confidence", "Notes",
        ]
        edited = st.data_editor(
            receipt_rows[receipt_columns],
            hide_index=True,
            width="stretch",
            disabled=["RowId", "Confidence"],
            column_config={
                "RowId": st.column_config.NumberColumn("#"),
                "confirm": st.column_config.CheckboxColumn("OK", default=False),
                "ProductName": st.column_config.TextColumn("Προϊόν"),
                "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=0, step=1),
                "ExpiryDate": st.column_config.TextColumn("Λήξη YYYY-MM-DD"),
                "LotNumber": st.column_config.TextColumn("LOT"),
                "BarcodeOrGTIN": st.column_config.TextColumn("Barcode / GTIN"),
                "GTIN": st.column_config.TextColumn("GTIN"),
                "Strength": st.column_config.TextColumn("Περιεκτικότητα"),
                "Brand": st.column_config.TextColumn("Μάρκα"),
                "SerialNumber": st.column_config.TextColumn("SN"),
            },
            key="csa_receipt_editor",
        )
        chosen = edited[edited["confirm"] == True].copy()

        if st.button("💾 Πέρασε την παραλαβή στο stock", type="primary", disabled=chosen.empty, width="stretch"):
            try:
                location_id = int(location_label.split("-", 1)[0].strip())
                seed = csa.clean(st.session_state.get("csa_receipt_seed"))
                note = csa.source_note(
                    supplier=supplier,
                    reference=reference,
                    document_date=document_date.isoformat(),
                )
                txs = []
                for _, row in chosen.iterrows():
                    row_id = int(row["RowId"])
                    txs.append(csa.receipt_transaction(
                        row,
                        location_id=location_id,
                        transaction_id=f"csa-in-{seed}-{row_id:04d}",
                        source_note=note,
                    ))
                saved, duplicate = append_transactions(txs)
                st.success(f"Παραλαβή: {saved} κινήσεις αποθηκεύτηκαν. {duplicate} διπλότυπες αγνοήθηκαν.")
                if saved:
                    for key in ["csa_receipt_rows", "csa_receipt_warnings", "csa_receipt_seed"]:
                        st.session_state.pop(key, None)
                    st.rerun()
            except Exception as exc:
                st.error(f"Δεν αποθηκεύτηκε η παραλαβή: {exc}")

with issue_tab:
    st.subheader("Αφαίρεση όσων πέρασαν / έφυγαν")
    mode = st.radio(
        "Τρόπος",
        ["Χειροκίνητα", "AI από φωτογραφία / screenshot"],
        horizontal=True,
        key="csa_issue_mode",
    )
    try:
        snapshot = csa.stock_snapshot(fresh_data())
        summary = csa.product_summary(snapshot)
    except Exception as exc:
        snapshot = pd.DataFrame()
        summary = pd.DataFrame()
        st.error(f"Δεν φορτώθηκε το stock: {exc}")

    if summary.empty:
        st.info("Δεν υπάρχει διαθέσιμο stock για έξοδο.")
    elif mode == "Χειροκίνητα":
        query = st.text_input("Γράψε προϊόν, μάρκα ή barcode", key="csa_issue_query")
        matches = csa.filter_summary(summary, query)
        if csa.clean(query) and matches.empty:
            st.warning("Δεν βρέθηκε προϊόν.")
        elif not matches.empty:
            options = list(matches.index)
            selected_index = st.selectbox(
                "Προϊόν",
                options,
                format_func=lambda idx: stock_label(matches.loc[idx]),
                key="csa_issue_product",
            )
            selected = matches.loc[selected_index]
            available = int(selected["Stock"])
            qty = st.number_input(
                "Ποσότητα που έφυγε",
                min_value=1,
                max_value=max(1, available),
                value=1,
                step=1,
                key="csa_issue_qty",
            )
            reason = st.selectbox(
                "Αιτία",
                ["Πώληση / χορήγηση", "Χρήση / κατανάλωση", "Διόρθωση αποθέματος"],
                key="csa_issue_reason",
            )
            note = st.text_input("Σημείωση (προαιρετική)", key="csa_issue_note")
            if st.button("➖ Αφαίρεση από stock", type="primary", width="stretch"):
                try:
                    prefix = "csa-out-" + uuid.uuid4().hex[:18]
                    txs = csa.fefo_issue_transactions(
                        snapshot,
                        selected,
                        quantity=int(qty),
                        note=f"reason={reason}; {note}".strip(),
                        transaction_prefix=prefix,
                    )
                    saved, duplicate = append_transactions(txs)
                    st.success(f"Αφαιρέθηκαν {int(qty)} τεμάχια με FEFO. Κινήσεις: {saved}, διπλότυπα: {duplicate}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Δεν έγινε η έξοδος: {exc}")
    else:
        st.info(
            "Φωγράφισε ή ανέβασε screenshot λίστας με όσα πέρασαν. "
            "Το AI προτείνει προϊόν και ποσότητα, αλλά δεν αφαιρεί τίποτα πριν το επιβεβαιώσεις."
        )
        out_uploads = st.file_uploader(
            "Φωτογραφίες / screenshots εξόδου",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key="csa_issue_uploads",
        )
        if st.button("✨ Ανάλυση εξόδου με AI", type="primary", disabled=not out_uploads, width="stretch"):
            try:
                images = images_from_uploads(out_uploads)
                with st.spinner("Διαβάζω τι έφυγε..."):
                    result = ai_inventory.analyze_images(
                        images,
                        api_key=st.secrets["OPENAI_API_KEY"],
                        mode="Έξοδος / πωλήσεις",
                        default_vat=DEFAULT_VAT,
                        model=model,
                    )
                st.session_state["csa_issue_rows"] = csa.normalize_ai_items(result.get("items", []), DEFAULT_VAT)
                st.session_state["csa_issue_warnings"] = result.get("warnings", [])
            except Exception as exc:
                st.error(f"Αποτυχία ανάλυσης εξόδου: {exc}")

        for warning in st.session_state.get("csa_issue_warnings", []):
            st.warning(csa.clean(warning))

        issue_rows = st.session_state.get("csa_issue_rows")
        if isinstance(issue_rows, pd.DataFrame) and not issue_rows.empty:
            issue_edited = st.data_editor(
                issue_rows[["RowId", "confirm", "ProductName", "Brand", "BarcodeOrGTIN", "GTIN", "Strength", "Quantity", "Confidence", "Notes"]],
                hide_index=True,
                width="stretch",
                disabled=["RowId", "Confidence"],
                column_config={
                    "confirm": st.column_config.CheckboxColumn("OK", default=False),
                    "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=0, step=1),
                },
                key="csa_issue_editor",
            )
            chosen_out = issue_edited[issue_edited["confirm"] == True].copy()
            if st.button("➖ Επιβεβαίωση και αφαίρεση", type="primary", disabled=chosen_out.empty, width="stretch"):
                try:
                    all_txs, errors = csa.ai_issue_transactions(
                        snapshot,
                        summary,
                        chosen_out,
                        note_prefix="source=CSA_AI_OUT",
                    )
                    if errors:
                        st.error("\n".join(errors))
                    if all_txs:
                        saved, duplicate = append_transactions(all_txs)
                        st.success(f"AI έξοδος: {saved} κινήσεις αποθηκεύτηκαν. {duplicate} διπλότυπα.")
                        st.session_state.pop("csa_issue_rows", None)
                        st.session_state.pop("csa_issue_warnings", None)
                        st.rerun()
                except Exception as exc:
                    st.error(f"Δεν έγινε η AI έξοδος: {exc}")

with stock_tab:
    st.subheader("Τρέχον stock και λήξεις")
    try:
        snapshot = csa.stock_snapshot(fresh_data())
        summary = csa.product_summary(snapshot)
        if summary.empty:
            st.info("Δεν υπάρχει ενεργό stock.")
        else:
            q = st.text_input("Αναζήτηση stock", key="csa_stock_query")
            shown = csa.filter_summary(summary, q)
            total_units = int(pd.to_numeric(shown["Stock"], errors="coerce").fillna(0).sum()) if not shown.empty else 0
            c1, c2 = st.columns(2)
            c1.metric("Κωδικοί / προϊόντα", len(shown))
            c2.metric("Σύνολο τεμαχίων", total_units)

            expiry_snapshot = core.add_expiry_columns(snapshot)
            expiring = expiry_snapshot[expiry_snapshot["ExpiryStatus"].isin(["expired", "expiring_soon"])].copy()
            if not expiring.empty:
                st.warning(f"Υπάρχουν {len(expiring)} παρτίδες ληγμένες ή με λήξη μέσα σε 90 ημέρες.")

            with st.expander("⏰ Παρτίδες / λήξεις", expanded=True):
                lot_cols = [
                    "Προϊόν", "Μάρκα", "LotNumber", "ExpiryDate", "ExpiryWarning",
                    "Τοποθεσία", "Stock", "GTIN", "Barcode",
                ]
                st.dataframe(expiry_snapshot[lot_cols], hide_index=True, width="stretch")

            st.markdown("#### Σύνολο ανά προϊόν")
            view_cols = ["Προϊόν", "Μάρκα", "Strength", "Barcode", "GTIN", "Τοποθεσία", "Stock", "Κατηγορία"]
            st.dataframe(shown[view_cols], hide_index=True, width="stretch")
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε το stock: {exc}")
