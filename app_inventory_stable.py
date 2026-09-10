import hashlib
import io
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
from barcode_lookup import lookup_barcode_online


LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}
DEFAULT_CATEGORY = "Άλλο"
STOCK_CACHE_TTL_SECONDS = 30
PRODUCT_CACHE_TTL_SECONDS = 60


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
    if products.empty:
        return None
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
    return None


def save_inventory_item(
    *,
    code: str,
    product_name: str,
    quantity: int,
    brand: str = "",
    strength: str = "",
    dosage_form: str = "",
    category: str = DEFAULT_CATEGORY,
    location_id: int = 0,
    note: str = "",
) -> None:
    code = clean(code)
    product_name = clean(product_name)
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
        location_id=location_id,
        movement="Απογραφή / καταμέτρηση (+)",
        quantity=int(quantity),
        delta=int(quantity),
        note=note or "source=barcode_inventory; verified_by_user=true",
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
    ]:
        st.session_state.pop(key, None)
    for key in list(st.session_state.keys()):
        if key.startswith(("product_name_", "brand_", "strength_", "form_", "package_", "confirm_", "quantity_", "location_")):
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
            if live_value:
                detected = validated_live_barcode(live_value)
                if detected:
                    st.session_state["active_barcode"] = detected
                    st.session_state.pop("lookup_candidates", None)
                    st.success(f"Διαβάστηκε: {detected}")
                else:
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
                    st.session_state["active_barcode"] = detected
                    st.session_state.pop("lookup_candidates", None)
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
    location_label = st.selectbox(
        "Τοποθεσία",
        [f"{idx} - {name}" for idx, name in LOCATIONS.items()],
        key=f"location_{context}",
    )
    location_id = int(location_label.split("-", 1)[0].strip())

    if st.button("💾 Αποθήκευση", type="primary", width="stretch", key=f"save_{context}"):
        try:
            save_inventory_item(
                code=code,
                product_name=product_name,
                quantity=int(quantity),
                brand=brand,
                strength=strength,
                dosage_form=dosage_form,
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
        return pd.DataFrame(columns=["ProductName", "Quantity", "Barcode", "OK"])
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
            rows.append({"ProductName": product, "Quantity": qty, "Barcode": "", "OK": False})
    return pd.DataFrame(rows, columns=["ProductName", "Quantity", "Barcode", "OK"])


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
                rows = pd.DataFrame(columns=["ProductName", "Quantity", "Barcode", "OK"])
            st.session_state["invoice_rows"] = pd.concat(
                [rows, pd.DataFrame([{"ProductName": clean(name), "Quantity": int(qty), "Barcode": "", "OK": False}])],
                ignore_index=True,
            )

    rows = st.session_state.get("invoice_rows")
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return

    st.markdown("**Βάλε barcode σε κάθε γραμμή και τσέκαρε OK.**")
    edited = st.data_editor(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "ProductName": st.column_config.TextColumn("Προϊόν"),
            "Quantity": st.column_config.NumberColumn("Ποσότητα", min_value=1, step=1),
            "Barcode": st.column_config.TextColumn("Barcode"),
            "OK": st.column_config.CheckboxColumn("OK"),
        },
        key="invoice_editor",
    )
    st.session_state["invoice_rows"] = edited

    chosen = edited[edited["OK"] == True].copy()
    invalid = chosen[chosen["Barcode"].astype(str).str.strip().eq("")]
    if not invalid.empty:
        st.warning("Υπάρχουν επιβεβαιωμένες γραμμές χωρίς barcode. Αυτές δεν θα περάσουν.")

    location_label = st.selectbox("Παραλαβή σε", [f"{idx} - {name}" for idx, name in LOCATIONS.items()], key="invoice_location")
    location_id = int(location_label.split("-", 1)[0].strip())
    valid = chosen[chosen["Barcode"].astype(str).str.strip().ne("")]

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
                save_inventory_item(
                    code=clean(row["Barcode"]),
                    product_name=clean(row["ProductName"]),
                    quantity=int(row["Quantity"]),
                    location_id=location_id,
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
    query = st.text_input("Αναζήτηση", placeholder="όνομα, barcode, μάρκα...")
    if query:
        stock, _ = core.search_stock(stock, query)
    columns = ["Προϊόν", "Μάρκα", "Barcode", "GTIN", "Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος", "Σύνολο"]
    available = [column for column in columns if column in stock.columns]
    st.dataframe(stock[available], hide_index=True, width="stretch")


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

    tab_scan, tab_invoice, tab_stock = st.tabs(["📷 Barcode", "🧾 Τιμολόγια", "📦 Stock"])
    with tab_scan:
        scan_tab()
    with tab_invoice:
        invoice_tab()
    with tab_stock:
        stock_tab()


if __name__ == "__main__":
    main()
