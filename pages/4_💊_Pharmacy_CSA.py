import uuid
from datetime import date

import pandas as pd
import streamlit as st

import ai_inventory
import app_inventory_search as core
import inventory_csa as csa


LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}


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


st.set_page_config(page_title="Pharmacy CSA", page_icon="💊", layout="wide")
st.title("💊 Pharmacy CSA")
st.caption("Μικρό σύστημα αποθήκης: τιμολόγιο/οθόνη → AI → επιβεβαίωση → παραλαβή, και ό,τι φεύγει αφαιρείται με ασφαλή κίνηση. Το AI διαβάζει, εσύ εγκρίνεις. Έτσι αποφεύγουμε να κάνει απογραφή ένα νευρωνικό δίκτυο με υπερβολική αυτοπεποίθηση.")

if "OPENAI_API_KEY" not in st.secrets:
    st.error("Λείπει το OPENAI_API_KEY από τα Streamlit Secrets.")
    st.stop()

model = csa.clean(st.secrets.get("OPENAI_MODEL", "gpt-5.6-terra"))
receipt_tab, issue_tab, stock_tab = st.tabs(["📥 AI Παραλαβή", "📤 Έξοδος", "📦 Τι υπάρχει"])

with receipt_tab:
    st.subheader("Τιμολόγιο ή οθόνη υπολογιστή")
    st.info("Από κινητό πάτησε το πεδίο φωτογραφιών και διάλεξε Κάμερα ή Gallery. Μπορείς να φωτογραφίσεις το χαρτί ή την οθόνη όπου φαίνονται οι γραμμές του τιμολογίου.")
    a, b, c = st.columns(3)
    supplier = a.text_input("Προμηθευτής", key="csa_supplier")
    reference = b.text_input("Αρ. τιμολογίου / αναφορά", key="csa_reference")
    document_date = c.date_input("Ημερομηνία παραστατικού", value=date.today(), key="csa_document_date")
    d, e = st.columns(2)
    location_label = d.selectbox("Παραλαβή σε", [f"{k} - {v}" for k, v in LOCATIONS.items()], key="csa_receipt_location")
    default_vat = e.number_input("Προεπιλεγμένος ΦΠΑ %", min_value=0.0, max_value=100.0, value=24.0, step=1.0, key="csa_receipt_vat")
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
            with st.spinner("Διαβάζω προϊόντα και ποσότητες..."):
                result = ai_inventory.analyze_images(
                    images,
                    api_key=st.secrets["OPENAI_API_KEY"],
                    mode="Τιμολόγιο / παραλαβή",
                    default_vat=default_vat,
                    model=model,
                )
            st.session_state["csa_receipt_rows"] = csa.normalize_ai_items(result.get("items", []), default_vat)
            st.session_state["csa_receipt_warnings"] = result.get("warnings", [])
            st.session_state["csa_receipt_seed"] = csa.batch_seed(
                [image["bytes"] for image in images],
                f"{supplier}|{reference}|{document_date.isoformat()}" if csa.clean(reference) else "",
            )
        except Exception as exc:
            st.error(f"Αποτυχία ανάλυσης: {exc}")

    for warning in st.session_state.get("csa_receipt_warnings", []):
        st.warning(csa.clean(warning))

    receipt_rows = st.session_state.get("csa_receipt_rows")
    if isinstance(receipt_rows, pd.DataFrame) and not receipt_rows.empty:
        st.caption("Τσέκαρε OK μόνο στις γραμμές που είναι σωστές. Διόρθωσε ποσότητα/κωδικό πριν από την αποθήκευση.")
        edited = st.data_editor(
            receipt_rows,
            hide_index=True,
            width="stretch",
            disabled=["RowId", "Confidence"],
            column_config={
                "confirm": st.column_config.CheckboxColumn("OK", default=False),
                "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=0, step=1),
                "NetPrice": st.column_config.NumberColumn("Καθαρή", format="%.2f €"),
                "VATRate": st.column_config.NumberColumn("ΦΠΑ %", format="%.2f"),
                "GrossPrice": st.column_config.NumberColumn("Τελική", format="%.2f €"),
            },
            key="csa_receipt_editor",
        )
        chosen = edited[edited["confirm"] == True].copy()
        if st.button("💾 Πέρασε την παραλαβή στο stock", type="primary", disabled=chosen.empty, width="stretch"):
            try:
                location_id = int(location_label.split("-", 1)[0].strip())
                seed = csa.clean(st.session_state.get("csa_receipt_seed"))
                note = csa.source_note(supplier=supplier, reference=reference, document_date=document_date.isoformat())
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
                    st.session_state.pop("csa_receipt_rows", None)
                    st.session_state.pop("csa_receipt_warnings", None)
                    st.session_state.pop("csa_receipt_seed", None)
                    st.rerun()
            except Exception as exc:
                st.error(f"Δεν αποθηκεύτηκε η παραλαβή: {exc}")

with issue_tab:
    st.subheader("Αφαίρεση όσων πέρασαν / έφυγαν")
    mode = st.radio("Τρόπος", ["Χειροκίνητα", "AI από φωτογραφία / screenshot"], horizontal=True, key="csa_issue_mode")
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
            qty = st.number_input("Ποσότητα που έφυγε", min_value=1, max_value=max(1, available), value=1, step=1, key="csa_issue_qty")
            reason = st.selectbox("Αιτία", ["Πώληση / χορήγηση", "Χρήση / κατανάλωση", "Διόρθωση αποθέματος"], key="csa_issue_reason")
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
        st.info("Φωτογράφισε ή ανέβασε screenshot λίστας με όσα πέρασαν. Το AI θα προτείνει προϊόν και ποσότητα, αλλά δεν αφαιρεί τίποτα πριν το επιβεβαιώσεις.")
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
                        default_vat=24.0,
                        model=model,
                    )
                st.session_state["csa_issue_rows"] = csa.normalize_ai_items(result.get("items", []), 24.0)
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
                    all_txs = []
                    errors = []
                    for _, item in chosen_out.iterrows():
                        candidates = csa.match_ai_item_to_summary(summary, item)
                        distinct = candidates[["CodeType", "CodeValue", "LocationId"]].drop_duplicates() if not candidates.empty else candidates
                        if candidates.empty:
                            errors.append(f"Δεν βρέθηκε στο stock: {csa.clean(item.get('ProductName')) or csa.clean(item.get('BarcodeOrGTIN'))}")
                            continue
                        if len(distinct) != 1:
                            errors.append(f"Αμφίβολη αντιστοίχιση: {csa.clean(item.get('ProductName'))}. Βάλε barcode ή κάνε χειροκίνητη έξοδο.")
                            continue
                        selected = candidates.iloc[0]
                        qty = int(item.get("Quantity", 0))
                        prefix = "csa-ai-out-" + uuid.uuid4().hex[:18]
                        all_txs.extend(csa.fefo_issue_transactions(
                            snapshot,
                            selected,
                            quantity=qty,
                            note="source=CSA_AI_OUT; " + csa.clean(item.get("Notes")),
                            transaction_prefix=prefix,
                        ))
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
    st.subheader("Τρέχον stock")
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
            view_cols = ["Προϊόν", "Μάρκα", "Strength", "Barcode", "GTIN", "Τοποθεσία", "Stock", "Κατηγορία"]
            st.dataframe(shown[view_cols], hide_index=True, width="stretch")
            with st.expander("Παρτίδες / λήξεις"):
                lot_cols = ["Προϊόν", "Μάρκα", "LotNumber", "ExpiryDate", "SerialNumber", "Τοποθεσία", "Stock", "GTIN", "Barcode"]
                st.dataframe(snapshot[lot_cols], hide_index=True, width="stretch")
    except Exception as exc:
        st.error(f"Δεν φορτώθηκε το stock: {exc}")
