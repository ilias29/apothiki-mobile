import re
from datetime import datetime

import cv2
import easyocr
import gspread
import numpy as np
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
SHEET_NAME = "Apothiki_Cloud"
WS_NAME = "Transactions"

LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}
CATEGORIES = ["Φάρμακο", "Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Άλλο"]
COLUMNS = [
    "Ημερομηνία", "Barcode", "Μάρκα", "Προϊόν", "Κατηγορία",
    "LocationId", "Τοποθεσία", "Κίνηση", "Ποσότητα", "DeltaQty", "Σημείωση"
]


def clean_barcode(value):
    return re.sub(r"\D", "", str(value or ""))


@st.cache_resource(show_spinner=False)
def get_ws():
    if "gcp_service_account" not in st.secrets:
        st.error("Λείπουν τα Streamlit Secrets: gcp_service_account. Βάλε πρώτα Google service account credentials.")
        st.stop()

    info = dict(st.secrets["gcp_service_account"])
    if "private_key" in info:
        info["private_key"] = info["private_key"].replace("\\n", "\n")

    creds = Credentials.from_service_account_info(info, scopes=SCOPE)
    client = gspread.authorize(creds)

    sheet_name = st.secrets.get("SHEET_NAME", SHEET_NAME)
    try:
        sh = client.open(sheet_name)
    except Exception:
        sh = client.create(sheet_name)

    try:
        ws = sh.worksheet(WS_NAME)
    except Exception:
        ws = sh.add_worksheet(title=WS_NAME, rows=5000, cols=30)
        ws.append_row(COLUMNS)

    headers = ws.row_values(1)
    if headers != COLUMNS:
        # Αν είναι άδειο ή παλιό, κρατάμε το υπάρχον αλλά προσθέτουμε headers μόνο αν δεν υπάρχουν.
        if not headers:
            ws.append_row(COLUMNS)
    return ws


def load_data():
    ws = get_ws()
    data = ws.get_all_records()
    df = pd.DataFrame(data)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[COLUMNS]


def append_row(row):
    ws = get_ws()
    ws.append_row([row.get(col, "") for col in COLUMNS])


@st.cache_resource(show_spinner=False)
def load_ocr_reader():
    return easyocr.Reader(["el", "en"], gpu=False)


def image_to_cv2(uploaded_file):
    nparr = np.frombuffer(uploaded_file.getvalue(), np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def detect_barcode_from_image(image):
    try:
        detector = cv2.barcode.BarcodeDetector()
        ok, decoded_info, _, _ = detector.detectAndDecode(image)
        if ok and decoded_info:
            for item in decoded_info:
                bc = clean_barcode(item)
                if bc:
                    return bc
    except Exception:
        pass
    return ""


def ocr_images(front_image=None, back_image=None):
    reader = load_ocr_reader()
    lines = []
    for img in [front_image, back_image]:
        if img is not None:
            try:
                lines.extend(reader.readtext(img, detail=0))
            except Exception:
                pass
    detected_name = max(lines, key=len) if lines else ""
    return detected_name, lines


def guess_category(text):
    t = str(text or "").lower()
    if any(x in t for x in ["vitamin", "omega", "magnesium", "zinc", "probiotic", "caps", "tabs", "d3", "b12"]):
        return "Συμπλήρωμα"
    if any(x in t for x in ["cream", "serum", "spf", "cleanser", "lotion"]):
        return "Καλλυντικό"
    return "Άλλο"


def make_stock_table(df):
    if df.empty:
        return pd.DataFrame(columns=["Barcode", "Μάρκα", "Προϊόν", "Κατηγορία", "Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος", "Σύνολο"])

    df = df.copy()
    for col in ["Barcode", "Μάρκα", "Προϊόν", "Κατηγορία"]:
        df[col] = df[col].astype(str).fillna("")
    df["DeltaQty"] = pd.to_numeric(df["DeltaQty"], errors="coerce").fillna(0)
    df["LocationId"] = pd.to_numeric(df["LocationId"], errors="coerce").fillna(-1).astype(int)

    grouped = df.groupby(["Barcode", "Μάρκα", "Προϊόν", "Κατηγορία", "LocationId"], dropna=False)["DeltaQty"].sum().reset_index()
    pivot = grouped.pivot_table(
        index=["Barcode", "Μάρκα", "Προϊόν", "Κατηγορία"],
        columns="LocationId",
        values="DeltaQty",
        fill_value=0,
    ).reset_index()
    pivot = pivot.rename(columns={0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"})
    for col in ["Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος"]:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot["Σύνολο"] = pivot["Αποθήκη"] + pivot["Κύριο Κτήριο"] + pivot["Πρώτος Όροφος"]
    return pivot.sort_values("Σύνολο", ascending=False)


st.set_page_config(page_title="Αποθήκη Φαρμακείου Cloud", page_icon="📦", layout="wide")
st.title("📦 Έξυπνη Αποθήκη Φαρμακείου")
st.caption("Cloud έκδοση: αποθήκευση σε Google Sheets, barcode-first, μάρκα, OCR, stock ανά χώρο.")
st.success("✅ Τρέχει το app_cloud.py με Google Sheets")

entry_tab, stock_tab, sales_tab, data_tab = st.tabs(["➕ Καταχώρηση", "📊 Stock / Search", "📅 Πωλήσεις / Περίοδος", "📄 Δεδομένα"])

with entry_tab:
    st.subheader("➕ Καταχώρηση Προϊόντος / Κίνησης")
    c1, c2 = st.columns(2)
    with c1:
        front_file = st.camera_input("Μπροστά φωτογραφία", key="front_cam") or st.file_uploader("Ή ανέβασε μπροστά", ["jpg", "jpeg", "png"], key="front_up")
    with c2:
        back_file = st.camera_input("Πίσω φωτογραφία / Barcode", key="back_cam") or st.file_uploader("Ή ανέβασε πίσω", ["jpg", "jpeg", "png"], key="back_up")

    front_image = image_to_cv2(front_file) if front_file else None
    back_image = image_to_cv2(back_file) if back_file else None
    detected_barcode = ""
    detected_name = ""
    ocr_lines = []

    if front_image is not None:
        st.image(front_image, caption="Μπροστά", use_container_width=True)
        detected_barcode = detect_barcode_from_image(front_image)
    if back_image is not None:
        st.image(back_image, caption="Πίσω", use_container_width=True)
        detected_barcode = detected_barcode or detect_barcode_from_image(back_image)
    if front_image is not None or back_image is not None:
        with st.spinner("OCR ανάλυση..."):
            detected_name, ocr_lines = ocr_images(front_image, back_image)
        if detected_barcode:
            st.success(f"Βρέθηκε πιθανό barcode: {detected_barcode}")
        if detected_name:
            st.info(f"Πιθανό προϊόν: {detected_name}")
        with st.expander("OCR αποτελέσματα"):
            st.write(ocr_lines)

    barcode = st.text_input("Barcode", value=detected_barcode)
    brand = st.text_input("Μάρκα", placeholder="π.χ. Panadol, Solgar, Vichy")
    product = st.text_input("Όνομα προϊόντος", value=detected_name)
    auto_cat = guess_category(" ".join(ocr_lines) + " " + product)
    category = st.selectbox("Κατηγορία", CATEGORIES, index=CATEGORIES.index(auto_cat) if auto_cat in CATEGORIES else 4)
    loc_label = st.selectbox("Τοποθεσία", [f"{k} - {v}" for k, v in LOCATIONS.items()])
    loc_id = int(loc_label.split("-")[0].strip())
    movement = st.selectbox("Κίνηση", ["Παραλαβή (+)", "Πώληση (-)", "Διόρθωση (+)", "Διόρθωση (-)"])
    qty = st.number_input("Ποσότητα", min_value=1, step=1, value=1)
    note = st.text_input("Σημείωση", placeholder="π.χ. αρχική απογραφή")

    if st.button("💾 Αποθήκευση στο Google Sheets", use_container_width=True):
        bc = clean_barcode(barcode)
        if not bc:
            st.error("Βάλε barcode. Χωρίς barcode θα φτιάξεις χάος με ωραίο UI.")
            st.stop()
        if not product.strip():
            st.error("Βάλε όνομα προϊόντος.")
            st.stop()
        delta = int(qty) if movement in ["Παραλαβή (+)", "Διόρθωση (+)"] else -int(qty)
        row = {
            "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Barcode": bc,
            "Μάρκα": brand.strip(),
            "Προϊόν": product.strip(),
            "Κατηγορία": category,
            "LocationId": loc_id,
            "Τοποθεσία": LOCATIONS[loc_id],
            "Κίνηση": movement,
            "Ποσότητα": int(qty),
            "DeltaQty": delta,
            "Σημείωση": note.strip(),
        }
        append_row(row)
        st.success(f"Αποθηκεύτηκε στο Google Sheets: {brand} {product} | Δ={delta}")

with stock_tab:
    st.subheader("📊 Stock / Search")
    df = load_data()
    stock = make_stock_table(df)
    search = st.text_input("🔎 Αναζήτηση με Barcode, Μάρκα ή Όνομα").strip().lower()
    if search:
        stock = stock[
            stock["Barcode"].astype(str).str.lower().str.contains(search, na=False)
            | stock["Μάρκα"].astype(str).str.lower().str.contains(search, na=False)
            | stock["Προϊόν"].astype(str).str.lower().str.contains(search, na=False)
        ]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Σύνολο", int(stock["Σύνολο"].sum()) if not stock.empty else 0)
    m2.metric("Αποθήκη", int(stock["Αποθήκη"].sum()) if not stock.empty else 0)
    m3.metric("Κύριο Κτήριο", int(stock["Κύριο Κτήριο"].sum()) if not stock.empty else 0)
    m4.metric("Πρώτος Όροφος", int(stock["Πρώτος Όροφος"].sum()) if not stock.empty else 0)
    st.dataframe(stock, use_container_width=True)

with sales_tab:
    st.subheader("📅 Πωλήσεις / Αφαιρέσεις ανά περίοδο")
    df = load_data()
    if df.empty:
        st.info("Δεν υπάρχουν δεδομένα.")
    else:
        df["Ημερομηνία_dt"] = pd.to_datetime(df["Ημερομηνία"], errors="coerce")
        a, b = st.columns(2)
        start = pd.to_datetime(a.date_input("Από ημερομηνία"))
        end = pd.to_datetime(b.date_input("Έως ημερομηνία")) + pd.Timedelta(days=1)
        period = df[(df["Ημερομηνία_dt"] >= start) & (df["Ημερομηνία_dt"] < end)].copy()
        sales = period[pd.to_numeric(period["DeltaQty"], errors="coerce").fillna(0) < 0].copy()
        if sales.empty:
            st.info("Δεν υπάρχουν πωλήσεις / αφαιρέσεις σε αυτό το διάστημα.")
        else:
            sales["Πωλήθηκαν"] = pd.to_numeric(sales["DeltaQty"], errors="coerce").fillna(0).abs()
            report = sales.groupby(["Barcode", "Μάρκα", "Προϊόν"])["Πωλήθηκαν"].sum().reset_index().sort_values("Πωλήθηκαν", ascending=False)
            st.dataframe(report, use_container_width=True)
            report["Πρόταση Να Ξαναμπεί"] = report["Πωλήθηκαν"]
            st.markdown("### Πρόταση αναπλήρωσης")
            st.dataframe(report, use_container_width=True)

with data_tab:
    st.subheader("📄 Όλες οι κινήσεις από Google Sheets")
    st.dataframe(load_data(), use_container_width=True)
