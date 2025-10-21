import streamlit as st
import easyocr
import cv2
import numpy as np
import pandas as pd
import os
from datetime import datetime

# Αρχείο αποθήκευσης (τοπικά – στο επόμενο στάδιο θα το κάνουμε cloud)
FILE_NAME = "apothiki_mobile.xlsx"

if not os.path.exists(FILE_NAME):
    df = pd.DataFrame(columns=["Ημερομηνία", "Προϊόν", "Ποσότητα", "Όροφος"])
    df.to_excel(FILE_NAME, index=False)

# Streamlit UI
st.set_page_config(page_title="Αποθήκη Φαρμακείου", page_icon="📦", layout="centered")
st.title("📱 Έξυπνη Αποθήκη Φαρμακείου")
st.subheader("Ανίχνευση προϊόντος μέσω κάμερας")

# Επιλογή ή λήψη φωτογραφίας
uploaded_file = st.camera_input("📸 Τράβηξε ή ανέβασε φωτογραφία προϊόντος")

if uploaded_file is not None:
    # Μετατροπή εικόνας
    bytes_data = uploaded_file.getvalue()
    nparr = np.frombuffer(bytes_data, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    st.image(image, caption="📦 Εικόνα προϊόντος", use_column_width=True)

    # OCR αναγνώριση
    with st.spinner("Ανάλυση εικόνας..."):
        reader = easyocr.Reader(['el', 'en'])
        results = reader.readtext(image, detail=0)

    if results:
        product_name = max(results, key=len)
        st.success(f"✅ Αναγνωρίστηκε: {product_name}")
    else:
        st.warning("⚠️ Δεν αναγνωρίστηκε προϊόν. Πληκτρολόγησέ το χειροκίνητα:")
        product_name = st.text_input("Όνομα προϊόντος")

    # Εισαγωγή ποσότητας και ορόφου
    qty = st.number_input("Ποσότητα", min_value=0, step=1)
    floor = st.text_input("Όροφος / Θέση")

    # Αποθήκευση
    if st.button("💾 Αποθήκευση"):
        df = pd.read_excel(FILE_NAME)
        new_row = {
            "Ημερομηνία": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Προϊόν": product_name,
            "Ποσότητα": qty,
            "Όροφος": floor
        }
        df.loc[len(df)] = new_row
        df.to_excel(FILE_NAME, index=False)
        st.success("📥 Το προϊόν αποθηκεύτηκε επιτυχώς!")
