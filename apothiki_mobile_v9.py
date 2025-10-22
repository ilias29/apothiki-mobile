import streamlit as st
import easyocr
import cv2
import numpy as np
import pandas as pd
import os
import pdfplumber
from datetime import datetime

# ---------- ΡΥΘΜΙΣΕΙΣ ----------
FILE_NAME = "apothiki_mobile.xlsx"

if not os.path.exists(FILE_NAME):
    df = pd.DataFrame(columns=["Ημερομηνία", "Προϊόν", "Ποσότητα", "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)", "Σύνολο"])
    df.to_excel(FILE_NAME, index=False)

# ---------- ΕΦΑΡΜΟΓΗ ----------
st.set_page_config(page_title="Έξυπνη Αποθήκη Φαρμακείου", page_icon="📦", layout="centered")
st.title("📱 Έξυπνη Αποθήκη Φαρμακείου v9")
st.subheader("Ανίχνευση προϊόντων, εκτίμηση ποσότητας & αυτόματη ενημέρωση")

# --- ΠΛΕΥΡΙΝΟ ΜΕΝΟΥ ---
mode = st.sidebar.selectbox("📋 Επιλογή λειτουργίας", [
    "Αναγνώριση προϊόντος (κάμερα ή αρχείο)",
    "Ανέβασε τιμολόγιο (PDF/Εικόνα)",
    "Προβολή αποθήκης"
])

# ---------- 1️⃣ ΚΑΜΕΡΑ / ΕΙΚΟΝΑ ΠΡΟΪΟΝΤΟΣ ----------
if mode == "Αναγνώριση προϊόντος (κάμερα ή αρχείο)":
    st.write("📸 Τράβηξε ή ανέβασε φωτογραφία προϊόντος")
    uploaded_file = st.camera_input("Τράβηξε φωτογραφία προϊόντος")

    # --- fallback επιλογή για κινητά που δεν δίνουν κάμερα ---
    if uploaded_file is None:
        st.info("📁 Εναλλακτικά, μπορείς να ανεβάσεις φωτογραφία από τα αρχεία σου:")
        uploaded_file = st.file_uploader("📂 Ανέβασε φωτογραφία προϊόντος", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        bytes_data = uploaded_file.getvalue()
        nparr = np.frombuffer(bytes_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        st.image(image, caption="📦 Εικόνα προϊόντος", use_column_width=True)

        with st.spinner("Ανάλυση εικόνας..."):
            # ⚙️ ΜΟΝΟ αγγλικά για σταθερή λειτουργία στο Cloud
            reader = easyocr.Reader(['en'])
            results = reader.readtext(image, detail=0)

        if results:
            product_name = max(results, key=len)
            st.success(f"✅ Αναγνωρίστηκε: {product_name}")
        else:
            st.warning("⚠️ Δεν αναγνωρίστηκε προϊόν. Πληκτρολόγησέ το χειροκίνητα:")
            product_name = st.text_input("Όνομα προϊόντος")

        # Εισαγωγή δεδομένων
        action = st.radio("Ενέργεια", ["➕ Προσθήκη", "➖ Αφαίρεση"], horizontal=True)
        qty = st.number_input("Ποσότητα", min_value=1, step=1)
        location = st.selectbox("Θέση προϊόντος", ["0 (Αποθήκη)", "1 (Μαγαζί)", "2 (Όροφος)"])

        if st.button("💾 Αποθήκευση / Ενημέρωση"):
            df = pd.read_excel(FILE_NAME)

            mask = (df["Προϊόν"].str.lower() == product_name.lower()) & (
                df["Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)"] == location)

            if mask.any():
                current_qty = int(df.loc[mask, "Ποσότητα"].values[-1])
                if "Προσθήκη" in action:
                    new_qty = current_qty + qty
                    st.info(f"📦 Προστέθηκαν {qty} τεμάχια στο '{product_name}' στη θέση {location}.")
                else:
                    new_qty = max(current_qty - qty, 0)
                    st.warning(f"📉 Αφαιρέθηκαν {qty} τεμάχια από '{product_name}' στη θέση {location}.")

                df.loc[mask, "Ποσότητα"] = new_qty
                df.loc[mask, "Ημερομηνία"] = datetime.now().strftime("%Y-%m-%d %H:%M")

                if new_qty == 0:
                    st.error(f"⚠️ Το προϊόν '{product_name}' έχει μηδενικό απόθεμα στη θέση {location}.")

            else:
                if "Αφαίρεση" in action:
                    st.error(f"⚠️ Δεν υπάρχει '{product_name}' στη θέση {location} για αφαίρεση.")
                else:
                    new_row = {
                        "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "Προϊόν": product_name,
                        "Ποσότητα": qty,
                        "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)": location,
                        "Σύνολο": 0
                    }
                    df.loc[len(df)] = new_row
                    st.success(f"✅ Δημιουργήθηκε νέα εγγραφή: '{product_name}' στη θέση {location} ({qty} τεμ.)")

            total_qty = df[df["Προϊόν"].str.lower() == product_name.lower()]["Ποσότητα"].sum()
            df.loc[df["Προϊόν"].str.lower() == product_name.lower(), "Σύνολο"] = total_qty

            if total_qty <= 3:
                st.error(f"⚠️ Χαμηλό συνολικό απόθεμα: '{product_name}' έχει μόνο {total_qty} τεμάχια συνολικά!")

            df.to_excel(FILE_NAME, index=False)
            st.info("📦 Η αποθήκη ενημερώθηκε επιτυχώς!")

# ---------- 2️⃣ ΑΝΕΒΑΣΜΑ ΤΙΜΟΛΟΓΙΟΥ ----------
elif mode == "Ανέβασε τιμολόγιο (PDF/Εικόνα)":
    uploaded_invoice = st.file_uploader("📜 Ανέβασε τιμολόγιο (PDF ή εικόνα)", type=["pdf", "png", "jpg", "jpeg"])
    if uploaded_invoice is not None:
        text = ""
        if uploaded_invoice.type == "application/pdf":
            with pdfplumber.open(uploaded_invoice) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        else:
            bytes_data = uploaded_invoice.getvalue()
            nparr = np.frombuffer(bytes_data, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            reader = easyocr.Reader(['en'])
            text = "\n".join(reader.readtext(image, detail=0))

        st.text_area("📄 Κείμενο που εντοπίστηκε:", text, height=200)

        df = pd.read_excel(FILE_NAME)
        added_products = []
        lines = text.splitlines()
        for line in lines:
            if any(x in line.lower() for x in ["τεμ", "pcs", "x"]):
                parts = line.split()
                if len(parts) >= 2:
                    product_name = " ".join(parts[:-1])
                    try:
                        qty = int(parts[-1].replace("x", "").replace("τεμ", "").replace("pcs", ""))
                    except:
                        qty = 1
                    new_row = {
                        "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "Προϊόν": product_name,
                        "Ποσότητα": qty,
                        "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)": "0 (Αποθήκη)",
                        "Σύνολο": 0
                    }
                    df.loc[len(df)] = new_row
                    added_products.append(f"{product_name} ({qty} τεμ.)")

        if added_products:
            for p in added_products:
                st.write("✅ Προστέθηκε από τιμολόγιο:", p)

            df["Σύνολο"] = df.groupby("Προϊόν")["Ποσότητα"].transform("sum")
            df.to_excel(FILE_NAME, index=False)
            st.success("📥 Τα προϊόντα από το τιμολόγιο καταχωρήθηκαν επιτυχώς!")
        else:
            st.warning("⚠️ Δεν εντοπίστηκαν προϊόντα στο τιμολόγιο.")

# ---------- 3️⃣ ΠΡΟΒΟΛΗ ΑΠΟΘΗΚΗΣ ----------
elif mode == "Προβολή αποθήκης":
    df = pd.read_excel(FILE_NAME)
    st.dataframe(df)

