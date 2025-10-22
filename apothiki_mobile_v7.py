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
BACKUP_FOLDER = "backups"

# Δημιουργία φακέλου backup αν δεν υπάρχει
if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)

if not os.path.exists(FILE_NAME):
    df = pd.DataFrame(columns=[
        "Ημερομηνία",
        "Προϊόν",
        "Ποσότητα",
        "Τύπος Ποσότητας",  # Ακριβής ή Εκτίμηση
        "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)",
        "Σύνολο"
    ])
    df.to_excel(FILE_NAME, index=False)

# ---------- ΕΦΑΡΜΟΓΗ ----------
st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="📦", layout="centered")
st.title("📱 Έξυπνη Αποθήκη Φαρμακείου v7")
st.subheader("Ανίχνευση προϊόντων, εκτίμηση ποσότητας & ενημέρωση ανά θέση")

# --- ΠΛΕΥΡΙΝΟ ΜΕΝΟΥ ---
mode = st.sidebar.selectbox("📋 Επιλογή λειτουργίας", [
    "Αναγνώριση προϊόντος (κάμερα)",
    "Ανέβασε τιμολόγιο (PDF/Εικόνα)",
    "Προβολή αποθήκης"
])

# ---------- 1️⃣ ΚΑΜΕΡΑ / ΕΙΚΟΝΑ ΠΡΟΪΟΝΤΟΣ ----------
if mode == "Αναγνώριση προϊόντος (κάμερα)":
    uploaded_file = st.camera_input("📸 Τράβηξε ή ανέβασε φωτογραφία προϊόντος")

    if uploaded_file is not None:
        bytes_data = uploaded_file.getvalue()
        nparr = np.frombuffer(bytes_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        st.image(image, caption="📦 Εικόνα προϊόντος", use_column_width=True)

        with st.spinner("Ανάλυση εικόνας..."):
            reader = easyocr.Reader(['el', 'en'])
            results = reader.readtext(image, detail=0)

        if results:
            product_name = max(results, key=len)
            st.success(f"✅ Αναγνωρίστηκε προϊόν: {product_name}")
        else:
            st.warning("⚠️ Δεν αναγνωρίστηκε προϊόν. Πληκτρολόγησέ το χειροκίνητα:")
            product_name = st.text_input("Όνομα προϊόντος")

        # 🔍 Εκτίμηση ποσότητας από την εικόνα
        estimated_qty = 1
        numbers_found = [int(s) for s in " ".join(results).split() if s.isdigit()]
        if numbers_found:
            estimated_qty = max(numbers_found)
            st.info(f"📊 Εκτιμώμενη ποσότητα: {estimated_qty} τεμάχια")

        # Ρωτάμε τον χρήστη αν είναι σωστή
        confirm = st.radio(
            "Είναι σωστή η ποσότητα που εντοπίστηκε;",
            ("Ναι ✅", "Όχι ❌", "Δεν είμαι σίγουρος / Εκτίμηση"),
            horizontal=True
        )

        if confirm == "Ναι ✅":
            qty = estimated_qty
            qty_type = "Ακριβής"
        elif confirm == "Όχι ❌":
            qty = st.number_input("Δώσε τη σωστή ποσότητα:", min_value=1, step=1)
            qty_type = "Ακριβής"
        else:
            qty = estimated_qty
            qty_type = "Εκτίμηση"
            st.warning(f"⚠️ Η ποσότητα για '{product_name}' θα καταχωρηθεί ως **εκτίμηση** ({estimated_qty} τεμ.)")

        # Επιλογή ενέργειας & θέσης
        action = st.radio("Ενέργεια", ["➕ Προσθήκη", "➖ Αφαίρεση"], horizontal=True)
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
                df.loc[mask, "Τύπος Ποσότητας"] = qty_type
                df.loc[mask, "Ημερομηνία"] = datetime.now().strftime("%Y-%m-%d %H:%M")

                if new_qty == 0:
                    st.error(f"⚠️ Το προϊόν '{product_name}' έχει μηδενικό απόθεμα στη θέση {location}.")
                    st.toast(f"🚨 Το προϊόν {product_name} εξαντλήθηκε!", icon="🚨")

            else:
                if "Αφαίρεση" in action:
                    st.error(f"⚠️ Δεν υπάρχει '{product_name}' στη θέση {location} για αφαίρεση.")
                else:
                    new_row = {
                        "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "Προϊόν": product_name,
                        "Ποσότητα": qty,
                        "Τύπος Ποσότητας": qty_type,
                        "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)": location,
                        "Σύνολο": 0
                    }
                    df.loc[len(df)] = new_row
                    st.success(f"✅ Δημιουργήθηκε νέα εγγραφή: '{product_name}' στη θέση {location} ({qty} τεμ., {qty_type})")

            # Υπολογισμός συνολικού αποθέματος
            total_qty = df[df["Προϊόν"].str.lower() == product_name.lower()]["Ποσότητα"].sum()
            df.loc[df["Προϊόν"].str.lower() == product_name.lower(), "Σύνολο"] = total_qty

            if total_qty <= 3:
                st.error(f"⚠️ Χαμηλό συνολικό απόθεμα: '{product_name}' έχει μόνο {total_qty} τεμάχια συνολικά!")

            # ----------- ΑΠΟΘΗΚΕΥΣΗ & BACKUP -----------
            df.to_excel(FILE_NAME, index=False)
            df.to_csv("apothiki_mobile.csv", index=False)

            backup_name = f"{BACKUP_FOLDER}/backup_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
            df.to_excel(backup_name, index=False)

            st.success(f"💾 Αποθηκεύτηκε επιτυχώς & δημιουργήθηκε backup ({backup_name})")
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
            reader = easyocr.Reader(['el', 'en'])
            text = "\n".join(reader.readtext(image, detail=0))

        st.text_area("📄 Κείμενο που εντοπίστηκε:", text, height=200)

        df = pd.read_excel(FILE_NAME)
        added_products = []
        lines = text.splitlines()
        for line in lines:
            if any(x in line.lower() for x in ["τεμ", "τεμάχια", "pcs", "x"]):
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
                        "Τύπος Ποσότητας": "Ακριβής",
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
            df.to_csv("apothiki_mobile.csv", index=False)
            st.success("📥 Τα προϊόντα από το τιμολόγιο καταχωρήθηκαν επιτυχώς!")
        else:
            st.warning("⚠️ Δεν εντοπίστηκαν προϊόντα στο τιμολόγιο.")

# ---------- 3️⃣ ΠΡΟΒΟΛΗ ΑΠΟΘΗΚΗΣ ----------
elif mode == "Προβολή αποθήκης":
    df = pd.read_excel(FILE_NAME)

    # ---------- ΡΥΘΜΙΣΕΙΣ ΕΜΦΑΝΙΣΗΣ ----------
    LOW_STOCK_LIMIT = 3

    def color_stock_rows(df):
        def color_row(row):
            if row["Ποσότητα"] == 0:
                return ['background-color: #ffb3b3; color: black'] * len(row)
            elif row["Ποσότητα"] <= LOW_STOCK_LIMIT:
                return ['background-color: #ffe5b4; color: black'] * len(row)
            else:
                return ['background-color: #e8ffe8; color: black'] * len(row)
        return df.style.apply(color_row, axis=1)

    # 🧮 Φίλτρο προβολής προϊόντων
    st.subheader("🔍 Επιλογή Προβολής")
    view_option = st.radio(
        "Εμφάνιση προϊόντων:",
        ("Όλα", "Χαμηλό απόθεμα (≤3)", "Εξαντλημένα (0)"),
        horizontal=True
    )

    if view_option == "Χαμηλό απόθεμα (≤3)":
        filtered_df = df[df["Ποσότητα"] <= LOW_STOCK_LIMIT]
    elif view_option == "Εξαντλημένα (0)":
        filtered_df = df[df["Ποσότητα"] == 0]
    else:
        filtered_df = df

    # Εμφάνιση ειδοποιήσεων
    low_stock = df[df["Ποσότητα"] <= LOW_STOCK_LIMIT]
    out_of_stock = df[df["Ποσότητα"] == 0]

    if not out_of_stock.empty:
        st.error("❌ Εξαντλημένα προϊόντα εντοπίστηκαν!")
        for _, row in out_of_stock.iterrows():
            st.toast(f"🚨 Το προϊόν {row['Προϊόν']} εξαντλήθηκε (θέση {row['Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)']})", icon="🚨")

    elif not low_stock.empty:
        st.warning("⚠️ Προσοχή! Ορισμένα προϊόντα έχουν χαμηλό απόθεμα.")

    # 📦 Εμφάνιση πίνακα με χρωματισμό
    st.subheader("📦 Πίνακας Αποθήκης")
    st.dataframe(color_stock_rows(filtered_df), use_container_width=True)
