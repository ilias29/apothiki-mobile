import streamlit as st
import easyocr
import cv2
import numpy as np
import pandas as pd
import os
import pdfplumber
import calendar
from datetime import datetime
import matplotlib.pyplot as plt

# ---------- ΡΥΘΜΙΣΕΙΣ ----------
FILE_NAME = "apothiki_mobile.xlsx"
BACKUP_FOLDER = "backups"

if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)

if not os.path.exists(FILE_NAME):
    df = pd.DataFrame(columns=[
        "Ημερομηνία",
        "Προϊόν",
        "Ποσότητα",
        "Τύπος Ποσότητας",
        "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)",
        "Σύνολο",
        "Ημερήσια Μεταβολή"
    ])
    df.to_excel(FILE_NAME, index=False)

# ---------- ΕΦΑΡΜΟΓΗ ----------
st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="📦", layout="centered")
st.title("📱 Έξυπνη Αποθήκη Φαρμακείου v8")
st.subheader("Ανίχνευση προϊόντων, εκτίμηση ποσότητας & αυτόματη ενημέρωση")

# --- ΠΛΕΥΡΙΝΟ ΜΕΝΟΥ ---
mode = st.sidebar.selectbox("📋 Επιλογή λειτουργίας", [
    "Αναγνώριση προϊόντος (κάμερα)",
    "Ανέβασε τιμολόγιο (PDF/Εικόνα)",
    "Προβολή αποθήκης"
])

# ---------- 📸 1️⃣ ΑΝΑΓΝΩΡΙΣΗ ΠΡΟΪΟΝΤΟΣ ----------
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

        # Εκτίμηση ποσότητας
        estimated_qty = 1
        numbers_found = [int(s) for s in " ".join(results).split() if s.isdigit()]
        if numbers_found:
            estimated_qty = max(numbers_found)
            st.info(f"📊 Εκτιμώμενη ποσότητα: {estimated_qty} τεμάχια")

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
            st.warning(f"⚠️ Η ποσότητα για '{product_name}' θα καταχωρηθεί ως **εκτίμηση**.")

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
                    st.info(f"📦 Προστέθηκαν {qty} τεμάχια στο '{product_name}'.")
                else:
                    new_qty = max(current_qty - qty, 0)
                    st.warning(f"📉 Αφαιρέθηκαν {qty} τεμάχια από '{product_name}'.")

                df.loc[mask, "Ποσότητα"] = new_qty
                df.loc[mask, "Τύπος Ποσότητας"] = qty_type
                df.loc[mask, "Ημερομηνία"] = datetime.now().strftime("%Y-%m-%d %H:%M")

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
                st.success(f"✅ Νέα εγγραφή: {product_name} ({qty} τεμ.)")

            # Υπολογισμός συνολικού αποθέματος
            total_qty = df[df["Προϊόν"].str.lower() == product_name.lower()]["Ποσότητα"].sum()
            df.loc[df["Προϊόν"].str.lower() == product_name.lower(), "Σύνολο"] = total_qty

            if total_qty <= 3:
                st.error(f"⚠️ Χαμηλό συνολικό απόθεμα: '{product_name}' έχει μόνο {total_qty} τεμάχια!")

            # ---------- Ημερήσια & Μηνιαία Μεταβολή ----------
            df = compare_and_log_daily_changes(df, product_name)

            # ---------- Αποθήκευση & Backup ----------
            df.to_excel(FILE_NAME, index=False)
            df.to_csv("apothiki_mobile.csv", index=False)

            backup_name = f"{BACKUP_FOLDER}/backup_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
            df.to_excel(backup_name, index=False)

            st.success(f"💾 Αποθηκεύτηκε επιτυχώς & δημιουργήθηκε backup ({backup_name})")
            st.info("📦 Η αποθήκη ενημερώθηκε επιτυχώς!")

# ---------- 📜 2️⃣ ΑΝΕΒΑΣΜΑ ΤΙΜΟΛΟΓΙΟΥ ----------
elif mode == "Ανέβασε τιμολόγιο (PDF/Εικόνα)":
    uploaded_invoice = st.file_uploader("📜 Ανέβασε τιμολόγιο", type=["pdf", "png", "jpg", "jpeg"])
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

# ---------- 📊 3️⃣ ΠΡΟΒΟΛΗ ΑΠΟΘΗΚΗΣ ----------
elif mode == "Προβολή αποθήκης":
    df = pd.read_excel(FILE_NAME)
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
        st.dataframe(color_stock_rows(filtered_df), use_container_width=True)
    # ---------- 📈 ΓΡΑΦΗΜΑ ΜΗΝΙΑΙΑΣ ΜΕΤΑΒΟΛΗΣ ----------
    st.subheader("📈 Γράφημα Μηνιαίας Μεταβολής")
    today = datetime.now()
    month_name = f"{calendar.month_name[today.month]} {today.year}"
    try:
        monthly_data = pd.read_excel(FILE_NAME, sheet_name=month_name)
        if not monthly_data.empty:
            fig, ax = plt.subplots()
            ax.bar(monthly_data["Προϊόν"], monthly_data["Συνολική Μεταβολή"], color="#4c9aff")
            ax.set_title(f"Μηνιαία Μεταβολή ({month_name})")
            ax.set_ylabel("Μεταβολή Τεμαχίων")
            plt.xticks(rotation=45, ha='right')
            st.pyplot(fig)
        else:
            st.info("📅 Δεν υπάρχουν ακόμα δεδομένα μηνιαίας μεταβολής.")
    except Exception:
        st.info("📅 Δεν υπάρχουν ακόμα δεδομένα μηνιαίας μεταβολής.")


    # ---------- 📉 ΣΥΓΚΡΙΣΗ & ΚΑΤΑΓΡΑΦΗ ΜΕΤΑΒΟΛΗΣ ----------
    def compare_and_log_daily_changes(df, product_name, file_name=FILE_NAME):
        same_product = df[df["Προϊόν"].str.lower() == product_name.lower()].sort_values("Ημερομηνία", ascending=False)

        # Δημιουργία στήλης αν δεν υπάρχει
        if "Ημερήσια Μεταβολή" not in df.columns:
            df["Ημερήσια Μεταβολή"] = ""

        # Υπολογισμός ημερήσιας μεταβολής
        if len(same_product) >= 2:
            latest_qty = same_product.iloc[0]["Ποσότητα"]
            prev_qty = same_product.iloc[1]["Ποσότητα"]
            change = latest_qty - prev_qty

            if change < 0:
                st.warning(f"⚠️ Το προϊόν **{product_name}** μειώθηκε κατά {abs(change)} τεμάχια.")
            elif change > 0:
                st.info(f"📦 Το προϊόν **{product_name}** αυξήθηκε κατά {change} τεμάχια.")
            else:
                st.success(f"✅ Καμία μεταβολή στην ποσότητα του προϊόντος **{product_name}**.")

            df.loc[same_product.index[0], "Ημερήσια Μεταβολή"] = change
        else:
            change = 0

        # ---------- Ενημέρωση μηνιαίας αναφοράς ----------
        today = datetime.now()
        month_name = f"{calendar.month_name[today.month]} {today.year}"

        # Διαβάζουμε ή δημιουργούμε το μηνιαίο φύλλο
        try:
            monthly_data = pd.read_excel(file_name, sheet_name=month_name)
        except Exception:
            monthly_data = pd.DataFrame(columns=["Προϊόν", "Συνολική Μεταβολή"])

        # Ενημέρωση ή προσθήκη γραμμής
        if product_name in monthly_data["Προϊόν"].values:
            monthly_data.loc[monthly_data["Προϊόν"] == product_name, "Συνολική Μεταβολή"] += change
        else:
            new_row = {"Προϊόν": product_name, "Συνολική Μεταβολή": change}
            monthly_data = pd.concat([monthly_data, pd.DataFrame([new_row])], ignore_index=True)

        # Ασφαλής εγγραφή στο φύλλο
        with pd.ExcelWriter(file_name, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            monthly_data.to_excel(writer, sheet_name=month_name, index=False)

        return df

