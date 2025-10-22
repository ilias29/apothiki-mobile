from __future__ import annotations

import streamlit as st
import easyocr
import cv2
import numpy as np
import pandas as pd
import os
import pdfplumber
from datetime import datetime
from rapidfuzz import process, fuzz

# ---------- ΡΥΘΜΙΣΕΙΣ ----------
FILE_NAME = "apothiki_mobile.xlsx"

if not os.path.exists(FILE_NAME):
    df = pd.DataFrame(columns=[
        "Ημερομηνία", "Προϊόν", "Μάρκα", "Κατηγορία",
        "Ποσότητα", "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)", "Σύνολο"
    ])
    df.to_excel(FILE_NAME, index=False)

# ---------- ΒΑΣΗ ΜΑΡΚΩΝ & ΛΕΞΕΩΝ-ΚΛΕΙΔΙΩΝ ----------
# Βασικές μάρκες συμπληρωμάτων + μεγάλες εταιρείες για γρήγορο ταίριασμα
SUPPLEMENT_BRANDS = [
    # Ζητήθηκαν ρητά
    "Solgar", "NOW", "NOW Foods", "HealthAid", "Lamberts", "Lanes",
    # Επιπλέον δημοφιλείς
    "Vitabiotics", "Nature's Bounty", "Power Health", "Superfoods Nature's Best",
    "A.Vogel", "Quest", "Thorne", "Jarrow", "Doctor's Best", "Swisse",
    "Garden of Life", "Optimum Nutrition", "Centrum", "Pharmaton", "EVIOL", "InterMed"
]

# Για γρήγορη αναγνώριση κατηγορίας
CATEGORY_KEYWORDS = {
    "Συμπλήρωμα": [
        "vitamin", "omega", "mg", "iu", "caps", "tablets", "softgels", "probiotic",
        "collagen", "zinc", "vit", "b-complex", "d3", "c 1000", "e 400", "magnesium",
        "curcumin", "turmeric", "coq10", "glucosamine", "chondroitin", "hyaluronic"
    ]
}

def guess_brand(text: str):
    """
    Επιστρέφει (προτεινόμενη_μάρκα, score) με fuzzy match πάνω στις SUPPLEMENT_BRANDS.
    """
    if not text:
        return None, 0
    best = process.extractOne(
        text, SUPPLEMENT_BRANDS, scorer=fuzz.WRatio
    )
    if best and best[1] >= 70:  # κατώφλι εμπιστοσύνης
        return best[0], best[1]
    return None, 0

def guess_category(text: str, brand: str | None):
    """
    Αν βρεθεί γνωστή μάρκα συμπληρωμάτων → 'Συμπλήρωμα'.
    Αλλιώς, αν εντοπίζονται λέξεις-κλειδιά → 'Συμπλήρωμα', διαφορετικά None.
    """
    if brand in SUPPLEMENT_BRANDS:
        return "Συμπλήρωμα"
    t = (text or "").lower()
    for kw in CATEGORY_KEYWORDS["Συμπλήρωμα"]:
        if kw in t:
            return "Συμπλήρωμα"
    return None

# ---------- ΕΦΑΡΜΟΓΗ ----------
st.set_page_config(page_title="Έξυπνη Αποθήκη Φαρμακείου", page_icon="📦", layout="centered")
st.title("📱 Έξυπνη Αποθήκη Φαρμακείου v10")
st.subheader("AI αναγνώριση συμπληρωμάτων (μάρκες) + επιβεβαίωση/διόρθωση")

mode = st.sidebar.selectbox("📋 Επιλογή λειτουργίας", [
    "Αναγνώριση προϊόντος (κάμερα ή αρχείο)",
    "Ανέβασε τιμολόγιο (PDF/Εικόνα)",
    "Προβολή αποθήκης"
])

# ---------- 1️⃣ AI ΑΝΑΓΝΩΡΙΣΗ ΠΡΟΪΟΝΤΟΣ ----------
if mode == "Αναγνώριση προϊόντος (κάμερα ή αρχείο)":
    st.write("📸 Τράβηξε ή ανέβασε φωτογραφία προϊόντος")
    uploaded_file = st.camera_input("Τράβηξε φωτογραφία προϊόντος")

    if uploaded_file is None:
        st.info("📁 Εναλλακτικά, ανέβασε φωτογραφία από τα αρχεία σου:")
        uploaded_file = st.file_uploader("📂 Ανέβασε φωτογραφία προϊόντος", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        # Διαβάζουμε την εικόνα
        img_bytes = uploaded_file.getvalue()
        nparr = np.frombuffer(img_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        st.image(image, caption="📦 Εικόνα προϊόντος", use_column_width=True)

        # OCR μόνο en για σταθερότητα στο Cloud
        with st.spinner("🔎 Ανάλυση εικόνας (OCR)…"):
            reader = easyocr.Reader(['en'])
            lines = reader.readtext(image, detail=0)

        # Συνένωση κειμένου για καλύτερο fuzzy matching
        ocr_text = " ".join(lines) if lines else ""
        st.caption(f"📄 OCR: {ocr_text[:140]}{'…' if len(ocr_text)>140 else ''}")

        # Προτεινόμενη μάρκα (AI fuzzy)
        brand, score = guess_brand(ocr_text)
        if brand:
            st.success(f"💊 Προτεινόμενη μάρκα: **{brand}** (score {score}%)")
        else:
            st.warning("⚠️ Δεν εντοπίστηκε μάρκα συμπληρώματος με σιγουριά.")

        # Προτεινόμενη κατηγορία
        auto_category = guess_category(ocr_text, brand) or "Άλλο"
        # Προτεινόμενο όνομα προϊόντος (με βάση το πιο “μακρύ” OCR κομμάτι)
        if lines:
            proposed_name = max(lines, key=len)
        else:
            proposed_name = ""

        # --- Φόρμα επιβεβαίωσης/διόρθωσης ---
        st.write("✏️ Επιβεβαίωσε ή διόρθωσε πριν την αποθήκευση:")
        col1, col2 = st.columns(2)
        with col1:
            product_name = st.text_input("Όνομα προϊόντος", value=proposed_name)
            category = st.selectbox("Κατηγορία", ["Συμπλήρωμα", "Φάρμακο", "Καλλυντικό", "Αναλώσιμο", "Άλλο"],
                                    index=(0 if auto_category=="Συμπλήρωμα" else 4))
        with col2:
            # Επιλογή μάρκας από λίστα + δυνατότητα ελεύθερης εισαγωγής
            brand_choices = ["(καμία)"] + SUPPLEMENT_BRANDS
            selected_brand = st.selectbox("Μάρκα (auto)", brand_choices,
                                          index=(brand_choices.index(brand) if brand in brand_choices else 0))
            if selected_brand == "(καμία)":
                selected_brand = st.text_input("Ή γράψε μάρκα", value="")

        action = st.radio("Ενέργεια", ["➕ Προσθήκη", "➖ Αφαίρεση"], horizontal=True)
        qty = st.number_input("Ποσότητα", min_value=1, step=1)
        location = st.selectbox("Θέση προϊόντος", ["0 (Αποθήκη)", "1 (Μαγαζί)", "2 (Όροφος)"])

        if st.button("💾 Αποθήκευση / Ενημέρωση"):
            if not product_name.strip():
                st.error("Δώσε όνομα προϊόντος.")
            else:
                df = pd.read_excel(FILE_NAME)

                # Βρίσκουμε εγγραφή ίδιου προϊόντος + ίδιας μάρκας + ίδιας θέσης
                mask = (
                    df["Προϊόν"].str.lower().fillna("") == product_name.lower()
                ) & (
                    df["Μάρκα"].str.lower().fillna("") == selected_brand.lower()
                ) & (
                    df["Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)"] == location
                )

                if mask.any():
                    current_qty = int(df.loc[mask, "Ποσότητα"].values[-1])
                    if "Προσθήκη" in action:
                        new_qty = current_qty + qty
                        st.info(f"📦 +{qty} τεμ. στο '{product_name}' ({selected_brand}) στη θέση {location}.")
                    else:
                        new_qty = max(current_qty - qty, 0)
                        st.warning(f"📉 -{qty} τεμ. από '{product_name}' ({selected_brand}) στη θέση {location}.")
                    df.loc[mask, "Ποσότητα"] = new_qty
                    df.loc[mask, "Κατηγορία"] = category
                    df.loc[mask, "Ημερομηνία"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                else:
                    if "Αφαίρεση" in action:
                        st.error(f"⚠️ Δεν υπάρχει '{product_name}' ({selected_brand}) στη θέση {location} για αφαίρεση.")
                    else:
                        new_row = {
                            "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "Προϊόν": product_name,
                            "Μάρκα": selected_brand,
                            "Κατηγορία": category,
                            "Ποσότητα": qty,
                            "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)": location,
                            "Σύνολο": 0
                        }
                        df.loc[len(df)] = new_row
                        st.success(f"✅ Δημιουργήθηκε νέα εγγραφή: '{product_name}' ({selected_brand}) στη θέση {location} ({qty} τεμ.)")

                # Υπολογισμός συνολικού αποθέματος ανά προϊόν (όλες οι θέσεις & μάρκες του ίδιου ονόματος)
                total_qty = df[df["Προϊόν"].str.lower() == product_name.lower()]["Ποσότητα"].sum()
                df.loc[df["Προϊόν"].str.lower() == product_name.lower(), "Σύνολο"] = total_qty

                # Ειδοποίηση χαμηλού στοκ
                if total_qty <= 3:
                    st.error(f"⚠️ Χαμηλό συνολικό απόθεμα: '{product_name}' έχει μόνο {total_qty} τεμάχια!")

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
            img_bytes = uploaded_invoice.getvalue()
            nparr = np.frombuffer(img_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            reader = easyocr.Reader(['en'])
            text = "\n".join(reader.readtext(image, detail=0))

        st.text_area("📄 Κείμενο που εντοπίστηκε:", text, height=200)

        df = pd.read_excel(FILE_NAME)
        added = []

        lines = text.splitlines()
        for line in lines:
            low = line.lower()
            # απλό heuristic: γραμμές που μοιάζουν με "Όνομα ... 3x" ή "… 3 pcs/τεμ"
            if any(x in low for x in [" pcs", " x", "τεμ"]):
                parts = line.split()
                if len(parts) >= 2:
                    # Μάρκα από τη γραμμή
                    m_brand, m_score = guess_brand(line)
                    # Όνομα προϊόντος = όλη η γραμμή χωρίς το τελευταίο token (που μοιάζει με qty)
                    product_guess = " ".join(parts[:-1])
                    # qty parse
                    tail = parts[-1].lower().replace("pcs", "").replace("τεμ", "").replace("x", "")
                    try:
                        qty = int(tail)
                    except:
                        qty = 1

                    new_row = {
                        "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "Προϊόν": product_guess.strip(),
                        "Μάρκα": (m_brand or ""),
                        "Κατηγορία": (guess_category(line, m_brand) or "Άλλο"),
                        "Ποσότητα": qty,
                        "Θέση (0=Αποθήκη,1=Μαγαζί,2=Όροφος)": "0 (Αποθήκη)",
                        "Σύνολο": 0
                    }
                    df.loc[len(df)] = new_row
                    added.append(f"{product_guess} ({m_brand or '—'}) x{qty}")

        if added:
            for a in added:
                st.write("✅ Από τιμολόγιο:", a)
            # ενημέρωσε σύνολα ανά προϊόν
            df["Σύνολο"] = df.groupby("Προϊόν")["Ποσότητα"].transform("sum")
            df.to_excel(FILE_NAME, index=False)
            st.success("📥 Καταχωρήθηκαν τα προϊόντα από το τιμολόγιο.")
        else:
            st.warning("⚠️ Δεν εντοπίστηκαν γραμμές προϊόντων στο τιμολόγιο.")

# ---------- 3️⃣ ΠΡΟΒΟΛΗ ΑΠΟΘΗΚΗΣ ----------
elif mode == "Προβολή αποθήκης":
    df = pd.read_excel(FILE_NAME)
    st.dataframe(df, use_container_width=True)
