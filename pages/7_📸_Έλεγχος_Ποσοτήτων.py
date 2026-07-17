import io
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Έλεγχος Ποσοτήτων", page_icon="📸", layout="wide")
st.title("📸 Έλεγχος ποσοτήτων από φωτογραφία")
st.caption(
    "Η ποσότητα βγαίνει από επιμέρους ορατές ομάδες, όχι από ένα μαγικό νούμερο. "
    "Έτσι εντοπίζεται αμέσως διπλομέτρηση ή χαμένο κουτί."
)

COUNT_COLUMNS = [
    "ProductName",
    "Strength",
    "Αριστερά",
    "Κέντρο",
    "Δεξιά",
    "Πίσω_ή_κρυμμένα",
    "TotalQuantity",
    "Confidence",
    "Notes",
]


def blank_rows(count: int = 12) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ProductName": "",
                "Strength": "",
                "Αριστερά": 0,
                "Κέντρο": 0,
                "Δεξιά": 0,
                "Πίσω_ή_κρυμμένα": 0,
                "TotalQuantity": 0,
                "Confidence": "Υψηλή",
                "Notes": "",
            }
            for _ in range(count)
        ],
        columns=COUNT_COLUMNS,
    )


def normalize_counts(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    count_parts = ["Αριστερά", "Κέντρο", "Δεξιά", "Πίσω_ή_κρυμμένα"]
    for column in count_parts:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0).clip(lower=0).astype(int)
    result["TotalQuantity"] = result[count_parts].sum(axis=1).astype(int)
    result["ProductName"] = result["ProductName"].fillna("").astype(str).str.strip().str.upper()
    result["Strength"] = result["Strength"].fillna("").astype(str).str.strip()
    result["Notes"] = result["Notes"].fillna("").astype(str).str.strip()
    result["Confidence"] = result["Confidence"].fillna("Χαμηλή").astype(str)
    return result[COUNT_COLUMNS]


if "photo_count_rows" not in st.session_state:
    st.session_state.photo_count_rows = blank_rows()

photo = st.file_uploader(
    "Φωτογραφία ραφιού / συρταριού",
    type=["jpg", "jpeg", "png"],
    help="Η φωτογραφία μένει μόνο στη συνεδρία και χρησιμοποιείται ως οπτική αναφορά.",
)
if photo is not None:
    st.image(photo, caption="Φωτογραφία αναφοράς", use_container_width=True)

st.subheader("Καταμέτρηση ανά ορατή περιοχή")
st.info(
    "Για κάθε προϊόν μέτρα χωριστά αριστερά, κέντρο, δεξιά και όσα φαίνονται πίσω/κρυμμένα. "
    "Το σύνολο υπολογίζεται αυτόματα. Μην ξαναμετράς το ίδιο κουτί σε δύο περιοχές."
)

edited = st.data_editor(
    st.session_state.photo_count_rows,
    key="photo_count_editor",
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    column_config={
        "ProductName": st.column_config.TextColumn("Προϊόν", required=False, width="large"),
        "Strength": st.column_config.TextColumn("Περιεκτικότητα", width="medium"),
        "Αριστερά": st.column_config.NumberColumn("Αριστερά", min_value=0, step=1),
        "Κέντρο": st.column_config.NumberColumn("Κέντρο", min_value=0, step=1),
        "Δεξιά": st.column_config.NumberColumn("Δεξιά", min_value=0, step=1),
        "Πίσω_ή_κρυμμένα": st.column_config.NumberColumn("Πίσω / κρυμμένα", min_value=0, step=1),
        "TotalQuantity": st.column_config.NumberColumn("Σύνολο", disabled=True),
        "Confidence": st.column_config.SelectboxColumn(
            "Βεβαιότητα", options=["Υψηλή", "Μέτρια", "Χαμηλή"], required=True
        ),
        "Notes": st.column_config.TextColumn("Σημείωση", width="large"),
    },
)

normalized = normalize_counts(edited)
st.session_state.photo_count_rows = normalized

left, middle, right = st.columns(3)
with left:
    if st.button("🔄 Υπολογισμός συνόλων", type="primary", use_container_width=True):
        st.session_state.photo_count_rows = normalized
        st.rerun()
with middle:
    if st.button("➕ 10 κενές γραμμές", use_container_width=True):
        st.session_state.photo_count_rows = pd.concat(
            [normalized, blank_rows(10)], ignore_index=True
        )
        st.rerun()
with right:
    if st.button("🧹 Καθαρισμός", use_container_width=True):
        st.session_state.photo_count_rows = blank_rows()
        st.rerun()

valid = normalized[normalized["ProductName"].ne("")].copy()

if not valid.empty:
    st.subheader("Έλεγχος πριν από import")
    duplicate_mask = valid.duplicated(subset=["ProductName", "Strength"], keep=False)
    if duplicate_mask.any():
        st.warning(
            "Υπάρχουν διπλές γραμμές για το ίδιο προϊόν/περιεκτικότητα. "
            "Ένωσέ τες πριν από το import, αλλιώς η ανθρωπότητα θα εφεύρει άλλη μία διπλοεγγραφή."
        )
        st.dataframe(valid.loc[duplicate_mask], use_container_width=True, hide_index=True)

    zero_rows = valid[valid["TotalQuantity"].eq(0)]
    if not zero_rows.empty:
        st.warning("Υπάρχουν προϊόντα με μηδενική ποσότητα.")

    st.metric("Σύνολο διαφορετικών γραμμών", len(valid))
    st.metric("Σύνολο ορατών κουτιών", int(valid["TotalQuantity"].sum()))

    audit_columns = [
        "ProductName",
        "Strength",
        "Αριστερά",
        "Κέντρο",
        "Δεξιά",
        "Πίσω_ή_κρυμμένα",
        "TotalQuantity",
        "Confidence",
        "Notes",
    ]
    st.dataframe(valid[audit_columns], use_container_width=True, hide_index=True)

    import_df = pd.DataFrame(
        {
            "Προϊόν": valid["ProductName"],
            "Strength": valid["Strength"],
            "Ποσότητα": valid["TotalQuantity"],
            "Κατηγορία": "Φάρμακο",
            "Τοποθεσία": "",
            "Σημείωση": valid.apply(
                lambda row: (
                    f"Photo count: L={row['Αριστερά']}, C={row['Κέντρο']}, "
                    f"R={row['Δεξιά']}, hidden={row['Πίσω_ή_κρυμμένα']}; "
                    f"confidence={row['Confidence']}"
                    + (f"; {row['Notes']}" if row["Notes"] else "")
                ),
                axis=1,
            ),
        }
    )

    csv_bytes = import_df.to_csv(index=False).encode("utf-8-sig")
    filename = f"photo_stock_count_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    st.download_button(
        "⬇️ Λήψη CSV για την εφαρμογή",
        data=csv_bytes,
        file_name=filename,
        mime="text/csv",
        use_container_width=True,
    )
else:
    st.caption("Συμπλήρωσε τουλάχιστον ένα προϊόν για να δημιουργηθεί αρχείο import.")
