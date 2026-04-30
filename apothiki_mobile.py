import streamlit as st
import easyocr
import cv2
import numpy as np
import pandas as pd
import os
from datetime import datetime

FILE_NAME = "apothiki_mobile.xlsx"

LOCATIONS = {
    0: "Αποθήκη",
    1: "Κύριο Κτήριο",
    2: "Πρώτος Όροφος"
}

COLUMNS = [
    "Ημερομηνία",
    "Barcode",
    "Προϊόν",
    "LocationId",
    "Τοποθεσία",
    "Κίνηση",
    "Ποσότητα",
    "DeltaQty",
    "Σημείωση"
]


# -----------------------------
# INIT FILE
# -----------------------------
def init_file():
    if not os.path.exists(FILE_NAME):
        df = pd.DataFrame(columns=COLUMNS)
        df.to_excel(FILE_NAME, index=False)


def load_data():
    init_file()
    return pd.read_excel(FILE_NAME)


def save_data(df):
    df.to_excel(FILE_NAME, index=False)


@st.cache_resource(show_spinner=False)
def load_ocr_reader():
    return easyocr.Reader(["el", "en"], gpu=False)


def image_to_cv2(uploaded_file):
    bytes_data = uploaded_file.getvalue()
    nparr = np.frombuffer(bytes_data, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def guess_product_from_image(image):
    reader = load_ocr_reader()
    results = reader.readtext(image, detail=0)

    if not results:
        return "", []

    product_name = max(results, key=len)
    return product_name, results


def make_stock_table(df):
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["DeltaQty"] = pd.to_numeric(df["DeltaQty"], errors="coerce").fillna(0)
    df["LocationId"] = pd.to_numeric(df["LocationId"], errors="coerce").fillna(-1).astype(int)

    stock = df.groupby(["Barcode", "Προϊόν", "LocationId"])["DeltaQty"].sum().reset_index()

    pivot = stock.pivot_table(
        index=["Barcode", "Προϊόν"],
        columns="LocationId",
        values="DeltaQty",
        fill_value=0
    ).reset_index()

    pivot = pivot.rename(columns={
        0: "Αποθήκη",
        1: "Κύριο Κτήριο",
        2: "Πρώτος Όροφος"
    })

    for col in ["Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος"]:
        if col not in pivot.columns:
            pivot[col] = 0

    pivot["Σύνολο"] = (
        pivot["Αποθήκη"] +
        pivot["Κύριο Κτήριο"] +
        pivot["Πρώτος Όροφος"]
    )

    return pivot.sort_values("Σύνολο", ascending=False)


# -----------------------------
# APP
# -----------------------------
init_file()

st.set_page_config(
    page_title="Αποθήκη Φαρμακείου",
    page_icon="📦",
    layout="wide"
)

st.title("📦 Έξυπνη Αποθήκη Φαρμακείου")
st.caption("Barcode-first αποθήκη με φωτογραφία, OCR, stock ανά χώρο και αναζήτηση.")

tab_entry, tab_stock, tab_sales, tab_data = st.tabs([
    "➕ Καταχώρηση",
    "📊 Stock",
    "📅 Πωλήσεις / Περίοδος",
    "📄 Δεδομένα"
])


# -----------------------------
# TAB 1 — ENTRY
# -----------------------------
with tab_entry:
    st.subheader("➕ Καταχώρηση Προϊόντος / Κίνησης")

    uploaded_file = st.camera_input("📸 Τράβηξε φωτογραφία προϊόντος")

    detected_name = ""
    ocr_lines = []

    if uploaded_file is not None:
        image = image_to_cv2(uploaded_file)
        st.image(image, caption="Εικόνα προϊόντος", use_container_width=True)

        with st.spinner("Ανάλυση εικόνας με OCR..."):
            detected_name, ocr_lines = guess_product_from_image(image)

        if detected_name:
            st.success(f"Πιθανό προϊόν: {detected_name}")
        else:
            st.warning("Δεν αναγνωρίστηκε καθαρά προϊόν. Θα το γράψεις χειροκίνητα, όπως στην παλιά εποχή των ανθρώπων.")

        with st.expander("Δες όλα τα OCR αποτελέσματα"):
            st.write(ocr_lines)

    st.markdown("### Στοιχεία προϊόντος")

    barcode = st.text_input("Barcode", placeholder="Πληκτρολόγησε ή σκάναρε barcode")
    product_name = st.text_input("Όνομα προϊόντος", value=detected_name)

    location_label = st.selectbox(
        "Τοποθεσία",
        [f"{k} - {v}" for k, v in LOCATIONS.items()]
    )

    location_id = int(location_label.split("-")[0].strip())
    location_name = LOCATIONS[location_id]

    movement = st.selectbox(
        "Κίνηση",
        ["Παραλαβή (+)", "Πώληση (-)", "Διόρθωση (+)", "Διόρθωση (-)"]
    )

    qty = st.number_input("Ποσότητα", min_value=1, step=1, value=1)
    note = st.text_input("Σημείωση", placeholder="π.χ. αρχική καταχώρηση, βραδινή αφαίρεση, απογραφή")

    if st.button("💾 Αποθήκευση Κίνησης", use_container_width=True):
        if not barcode.strip():
            st.error("Βάλε barcode. Το barcode είναι η ταυτότητα του προϊόντος, όχι διακοσμητικό.")
            st.stop()

        if not product_name.strip():
            st.error("Βάλε όνομα προϊόντος.")
            st.stop()

        if movement in ["Παραλαβή (+)", "Διόρθωση (+)"]:
            delta = qty
        else:
            delta = -qty

        df = load_data()

        new_row = {
            "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Barcode": str(barcode).strip(),
            "Προϊόν": product_name.strip(),
            "LocationId": location_id,
            "Τοποθεσία": location_name,
            "Κίνηση": movement,
            "Ποσότητα": qty,
            "DeltaQty": delta,
            "Σημείωση": note.strip()
        }

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_data(df)

        st.success(f"Αποθηκεύτηκε: {product_name} | {location_name} | Δ={delta}")


# -----------------------------
# TAB 2 — STOCK
# -----------------------------
with tab_stock:
    st.subheader("📊 Stock ανά χώρο")

    df = load_data()

    if df.empty:
        st.info("Δεν υπάρχουν ακόμα δεδομένα.")
    else:
        stock_table = make_stock_table(df)

        search = st.text_input("🔎 Αναζήτηση με Barcode ή Όνομα").strip().lower()

        if search:
            stock_table = stock_table[
                stock_table["Barcode"].astype(str).str.lower().str.contains(search) |
                stock_table["Προϊόν"].astype(str).str.lower().str.contains(search)
            ]

        c1, c2, c3, c4 = st.columns(4)

        total_items = stock_table["Σύνολο"].sum() if not stock_table.empty else 0
        total_storage = stock_table["Αποθήκη"].sum() if "Αποθήκη" in stock_table else 0
        total_main = stock_table["Κύριο Κτήριο"].sum() if "Κύριο Κτήριο" in stock_table else 0
        total_floor = stock_table["Πρώτος Όροφος"].sum() if "Πρώτος Όροφος" in stock_table else 0

        c1.metric("Σύνολο", int(total_items))
        c2.metric("Αποθήκη", int(total_storage))
        c3.metric("Κύριο Κτήριο", int(total_main))
        c4.metric("Πρώτος Όροφος", int(total_floor))

        st.dataframe(stock_table, use_container_width=True)

        negative = stock_table[stock_table["Σύνολο"] < 0]

        if not negative.empty:
            st.error("⚠️ Υπάρχουν προϊόντα με αρνητικό stock. Κάπου έγινε λάθος κίνηση.")
            st.dataframe(negative, use_container_width=True)


# -----------------------------
# TAB 3 — SALES PERIOD
# -----------------------------
with tab_sales:
    st.subheader("📅 Πωλήσεις / Αφαιρέσεις ανά περίοδο")

    df = load_data()

    if df.empty:
        st.info("Δεν υπάρχουν δεδομένα.")
    else:
        df["Ημερομηνία_dt"] = pd.to_datetime(df["Ημερομηνία"], errors="coerce")

        col1, col2 = st.columns(2)

        with col1:
            start_date = st.date_input("Από ημερομηνία")

        with col2:
            end_date = st.date_input("Έως ημερομηνία")

        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date) + pd.Timedelta(days=1)

        period = df[
            (df["Ημερομηνία_dt"] >= start_dt) &
            (df["Ημερομηνία_dt"] < end_dt)
        ].copy()

        sales = period[period["DeltaQty"] < 0].copy()

        if sales.empty:
            st.info("Δεν υπάρχουν πωλήσεις / αφαιρέσεις σε αυτό το διάστημα.")
        else:
            sales["Πωλήθηκαν"] = sales["DeltaQty"].abs()

            report = sales.groupby(["Barcode", "Προϊόν"])["Πωλήθηκαν"].sum().reset_index()
            report = report.sort_values("Πωλήθηκαν", ascending=False)

            st.markdown("### Τι έφυγε")
            st.dataframe(report, use_container_width=True)

            st.markdown("### Πρόταση αναπλήρωσης")
            report["Πρόταση Να Ξαναμπεί"] = report["Πωλήθηκαν"]
            st.dataframe(report, use_container_width=True)


# -----------------------------
# TAB 4 — RAW DATA
# -----------------------------
with tab_data:
    st.subheader("📄 Όλες οι κινήσεις")

    df = load_data()

    if df.empty:
        st.info("Δεν υπάρχουν δεδομένα.")
    else:
        st.dataframe(df, use_container_width=True)

        st.download_button(
            "⬇️ Κατέβασε Excel",
            data=open(FILE_NAME, "rb").read(),
            file_name=FILE_NAME,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
