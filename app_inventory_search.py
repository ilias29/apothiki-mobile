import re
from datetime import datetime

import cv2
import gspread
import numpy as np
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "Apothiki_Cloud"
WS_NAME = "Transactions"
LOCATIONS = {0: "Αποθήκη", 1: "Κύριο Κτήριο", 2: "Πρώτος Όροφος"}
CATEGORIES = ["Φάρμακο", "Συμπλήρωμα", "Καλλυντικό", "Αναλώσιμο", "Άλλο"]
COLUMNS = ["Ημερομηνία", "CodeType", "CodeValue", "Barcode", "Μάρκα", "Προϊόν", "Κατηγορία", "LocationId", "Τοποθεσία", "Κίνηση", "Ποσότητα", "DeltaQty", "FrontPhotoUrl", "BackPhotoUrl", "Σημείωση"]

def clean_barcode(x):
    return re.sub(r"\D", "", str(x or ""))

def clean(x):
    return str(x or "").strip()

@st.cache_resource(show_spinner=False)
def ws():
    if "gcp_service_account" not in st.secrets:
        st.error("Λείπουν τα Streamlit Secrets: gcp_service_account")
        st.stop()
    info = dict(st.secrets["gcp_service_account"])
    if "private_key" in info:
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(info, scopes=SCOPE)
    client = gspread.authorize(creds)
    sh_name = st.secrets.get("SHEET_NAME", SHEET_NAME)
    try:
        sh = client.open(sh_name)
    except Exception:
        sh = client.create(sh_name)
    try:
        w = sh.worksheet(WS_NAME)
    except Exception:
        w = sh.add_worksheet(title=WS_NAME, rows=5000, cols=40)
        w.append_row(COLUMNS)
        return w
    headers = w.row_values(1)
    if not headers:
        w.append_row(COLUMNS)
    else:
        missing = [c for c in COLUMNS if c not in headers]
        if missing:
            w.update("A1", [headers + missing])
    return w

def load_data():
    data = ws().get_all_records()
    df = pd.DataFrame(data)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df["Barcode"] = df["Barcode"].astype(str).fillna("")
    df["CodeType"] = df["CodeType"].astype(str).fillna("")
    df["CodeValue"] = df["CodeValue"].astype(str).fillna("")
    mask = (df["CodeValue"].str.strip() == "") & (df["Barcode"].str.strip() != "")
    df.loc[mask, "CodeType"] = "Barcode"
    df.loc[mask, "CodeValue"] = df.loc[mask, "Barcode"]
    return df[COLUMNS]

def add_row(row):
    ws().append_row([row.get(c, "") for c in COLUMNS])

def to_img(file):
    arr = np.frombuffer(file.getvalue(), np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)

def detect_barcode(img):
    try:
        det = cv2.barcode.BarcodeDetector()
        ok, vals, _, _ = det.detectAndDecode(img)
        if ok and vals:
            for v in vals:
                b = clean_barcode(v)
                if b:
                    return b
    except Exception:
        pass
    return ""

def detect_qr(img):
    try:
        det = cv2.QRCodeDetector()
        val, _, _ = det.detectAndDecode(img)
        return clean(val)
    except Exception:
        return ""

def detect_code(front=None, back=None):
    for img in [back, front]:
        if img is not None:
            b = detect_barcode(img)
            if b:
                return "Barcode", b, b
    for img in [back, front]:
        if img is not None:
            q = detect_qr(img)
            if q:
                return "QR", q, ""
    return "Barcode", "", ""

def stock_table(df):
    cols = ["CodeType", "CodeValue", "Barcode", "Μάρκα", "Προϊόν", "Κατηγορία", "Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος", "Σύνολο", "FrontPhotoUrl", "BackPhotoUrl"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    d = df.copy()
    for c in ["CodeType", "CodeValue", "Barcode", "Μάρκα", "Προϊόν", "Κατηγορία", "FrontPhotoUrl", "BackPhotoUrl"]:
        d[c] = d[c].astype(str).fillna("")
    d["DeltaQty"] = pd.to_numeric(d["DeltaQty"], errors="coerce").fillna(0)
    d["LocationId"] = pd.to_numeric(d["LocationId"], errors="coerce").fillna(-1).astype(int)
    g = d.groupby(["CodeType", "CodeValue", "Barcode", "Μάρκα", "Προϊόν", "Κατηγορία", "LocationId"], dropna=False)["DeltaQty"].sum().reset_index()
    p = g.pivot_table(index=["CodeType", "CodeValue", "Barcode", "Μάρκα", "Προϊόν", "Κατηγορία"], columns="LocationId", values="DeltaQty", fill_value=0).reset_index()
    p = p.rename(columns={0:"Αποθήκη",1:"Κύριο Κτήριο",2:"Πρώτος Όροφος"})
    for c in ["Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος"]:
        if c not in p.columns:
            p[c] = 0
    p["Σύνολο"] = p["Αποθήκη"] + p["Κύριο Κτήριο"] + p["Πρώτος Όροφος"]
    photos = d.sort_values("Ημερομηνία").groupby("CodeValue", dropna=False)[["FrontPhotoUrl", "BackPhotoUrl"]].last().reset_index()
    return p.merge(photos, on="CodeValue", how="left").sort_values("Σύνολο", ascending=False)

def search_stock(stk, q):
    q = clean(q).lower()
    if not q or stk.empty:
        return stk, ""
    code = stk[stk["CodeValue"].astype(str).str.lower().str.contains(q, na=False) | stk["Barcode"].astype(str).str.lower().str.contains(q, na=False)]
    if not code.empty:
        return code, "Βρέθηκε με Barcode / QR"
    text = stk[stk["Μάρκα"].astype(str).str.lower().str.contains(q, na=False) | stk["Προϊόν"].astype(str).str.lower().str.contains(q, na=False) | stk["Κατηγορία"].astype(str).str.lower().str.contains(q, na=False)]
    if not text.empty:
        return text, "Βρέθηκε με Μάρκα / Όνομα / Κατηγορία"
    return stk.iloc[0:0], "Δεν βρέθηκε αποτέλεσμα"

def cards(stk):
    if stk.empty:
        st.info("Δεν υπάρχουν αποτελέσματα.")
        return
    for _, r in stk.head(20).iterrows():
        with st.container(border=True):
            a, b = st.columns([1,2])
            with a:
                u = clean(r.get("FrontPhotoUrl", ""))
                if u:
                    st.image(u, caption="Πρόσοψη", use_container_width=True)
                else:
                    st.info("Δεν υπάρχει φωτογραφία")
            with b:
                st.markdown(f"### {r.get('Προϊόν','')}")
                st.write(f"**Μάρκα:** {r.get('Μάρκα','') or '—'}")
                st.write(f"**Κωδικός:** {r.get('CodeType','')} | `{r.get('CodeValue','')}`")
                st.write(f"**Αποθήκη:** {int(r.get('Αποθήκη',0))}")
                st.write(f"**Κύριο Κτήριο:** {int(r.get('Κύριο Κτήριο',0))}")
                st.write(f"**Πρώτος Όροφος:** {int(r.get('Πρώτος Όροφος',0))}")
                st.success(f"Σύνολο: {int(r.get('Σύνολο',0))}")
                bu = clean(r.get("BackPhotoUrl", ""))
                if bu:
                    st.image(bu, caption="Πίσω / Barcode", use_container_width=True)

st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="📦", layout="wide")
st.title("📦 Αποθήκη Φαρμακείου")
st.caption("Πρώτα Barcode/QR, μετά όνομα/μάρκα, μετά φωτογραφία για επιβεβαίωση.")
st.success("✅ Τρέχει το app_inventory_search.py")

t1, t2, t3, t4 = st.tabs(["➕ Καταχώρηση", "🔎 Search", "📅 Πωλήσεις", "📄 Δεδομένα"])

with t1:
    st.subheader("Καταχώρηση")
    c1, c2 = st.columns(2)
    with c1:
        ff = st.camera_input("Φωτογραφία πρόσοψης", key="front") or st.file_uploader("Ή ανέβασε πρόσοψη", ["jpg","jpeg","png"], key="front_up")
    with c2:
        bf = st.camera_input("Φωτογραφία πίσω / Barcode / QR", key="back") or st.file_uploader("Ή ανέβασε πίσω", ["jpg","jpeg","png"], key="back_up")
    fi = to_img(ff) if ff else None
    bi = to_img(bf) if bf else None
    if fi is not None: st.image(fi, caption="Πρόσοψη", use_container_width=True)
    if bi is not None: st.image(bi, caption="Πίσω", use_container_width=True)
    dt, dc, db = detect_code(fi, bi)
    if dc: st.success(f"Βρέθηκε {dt}: {dc}")
    ctype = st.selectbox("Τύπος κωδικού", ["Barcode", "QR", "Other"], index=["Barcode","QR","Other"].index(dt) if dt in ["Barcode","QR","Other"] else 0)
    cval = st.text_input("Barcode / QR", value=dc)
    brand = st.text_input("Μάρκα")
    product = st.text_input("Όνομα προϊόντος")
    cat = st.selectbox("Κατηγορία", CATEGORIES)
    front_url = st.text_input("URL φωτογραφίας πρόσοψης")
    back_url = st.text_input("URL φωτογραφίας πίσω/barcode")
    loc_label = st.selectbox("Τοποθεσία", [f"{k} - {v}" for k,v in LOCATIONS.items()])
    loc_id = int(loc_label.split("-")[0].strip())
    move = st.selectbox("Κίνηση", ["Παραλαβή (+)", "Πώληση (-)", "Διόρθωση (+)", "Διόρθωση (-)"])
    qty = st.number_input("Ποσότητα", min_value=1, value=1, step=1)
    note = st.text_input("Σημείωση")
    if st.button("💾 Αποθήκευση", use_container_width=True):
        final = clean(cval)
        if not final:
            st.error("Βάλε Barcode ή QR."); st.stop()
        barcode = clean_barcode(final) if ctype == "Barcode" else ""
        if ctype == "Barcode" and not barcode:
            st.error("Διάλεξες Barcode αλλά δεν έβαλες αριθμητικό barcode."); st.stop()
        if not clean(product):
            st.error("Βάλε όνομα προϊόντος."); st.stop()
        delta = int(qty) if move in ["Παραλαβή (+)", "Διόρθωση (+)"] else -int(qty)
        row = {"Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"), "CodeType": ctype, "CodeValue": barcode if ctype == "Barcode" else final, "Barcode": barcode, "Μάρκα": brand.strip(), "Προϊόν": product.strip(), "Κατηγορία": cat, "LocationId": loc_id, "Τοποθεσία": LOCATIONS[loc_id], "Κίνηση": move, "Ποσότητα": int(qty), "DeltaQty": delta, "FrontPhotoUrl": front_url.strip(), "BackPhotoUrl": back_url.strip(), "Σημείωση": note.strip()}
        add_row(row)
        st.success("Αποθηκεύτηκε στο Google Sheets.")

with t2:
    st.subheader("Search")
    s = stock_table(load_data())
    q = st.text_input("Γράψε Barcode, QR, Μάρκα ή Όνομα", placeholder="π.χ. Depon ή 520...")
    res, msg = search_stock(s, q)
    if msg: st.info(msg)
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Σύνολο", int(res["Σύνολο"].sum()) if not res.empty else 0)
    m2.metric("Αποθήκη", int(res["Αποθήκη"].sum()) if not res.empty else 0)
    m3.metric("Κύριο Κτήριο", int(res["Κύριο Κτήριο"].sum()) if not res.empty else 0)
    m4.metric("Πρώτος Όροφος", int(res["Πρώτος Όροφος"].sum()) if not res.empty else 0)
    cards(res)
    with st.expander("Πίνακας"):
        st.dataframe(res, use_container_width=True)

with t3:
    st.subheader("Πωλήσεις")
    df = load_data()
    if df.empty:
        st.info("Δεν υπάρχουν δεδομένα.")
    else:
        df["Ημερομηνία_dt"] = pd.to_datetime(df["Ημερομηνία"], errors="coerce")
        a,b = st.columns(2)
        start = pd.to_datetime(a.date_input("Από"))
        end = pd.to_datetime(b.date_input("Έως")) + pd.Timedelta(days=1)
        d = df[(df["Ημερομηνία_dt"] >= start) & (df["Ημερομηνία_dt"] < end)]
        sales = d[pd.to_numeric(d["DeltaQty"], errors="coerce").fillna(0) < 0].copy()
        if sales.empty:
            st.info("Δεν υπάρχουν αφαιρέσεις.")
        else:
            sales["Πωλήθηκαν"] = pd.to_numeric(sales["DeltaQty"], errors="coerce").fillna(0).abs()
            rep = sales.groupby(["CodeType","CodeValue","Barcode","Μάρκα","Προϊόν"])["Πωλήθηκαν"].sum().reset_index().sort_values("Πωλήθηκαν", ascending=False)
            st.dataframe(rep, use_container_width=True)

with t4:
    st.subheader("Όλες οι κινήσεις")
    st.dataframe(load_data(), use_container_width=True)
