import re
import uuid
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

LOCATIONS = {
    0: "Αποθήκη",
    1: "Κύριο Κτήριο",
    2: "Πρώτος Όροφος",
}

CATEGORIES = [
    "Φάρμακο",
    "Συμπλήρωμα",
    "Καλλυντικό",
    "Αναλώσιμο",
    "Άλλο",
]

COLUMNS = [
    "TransactionId",
    "Timestamp",
    "Ημερομηνία",
    "CodeType",
    "CodeValue",
    "Barcode",
    "Μάρκα",
    "Προϊόν",
    "Κατηγορία",
    "LocationId",
    "Τοποθεσία",
    "Κίνηση",
    "Ποσότητα",
    "DeltaQty",
    "FrontPhotoUrl",
    "BackPhotoUrl",
    "Σημείωση",
    "Voided",
    "VoidOf",
]


def clean_barcode(value):
    return re.sub(r"\D", "", str(value or ""))


def clean(value):
    return str(value or "").strip()


def show_sheet_error(action, exc):
    st.error(
        f"Αποτυχία Google Sheets κατά την ενέργεια «{action}». "
        "Έλεγξε τα secrets, την κοινή χρήση του Sheet και τη σύνδεση."
    )
    st.caption(f"Τεχνική λεπτομέρεια: {exc}")


@st.cache_resource(show_spinner=False)
def worksheet():
    if "gcp_service_account" not in st.secrets:
        st.error("Λείπουν τα Streamlit Secrets: gcp_service_account.")
        st.stop()

    try:
        info = dict(st.secrets["gcp_service_account"])
        if "private_key" in info:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        creds = Credentials.from_service_account_info(info, scopes=SCOPE)
        client = gspread.authorize(creds)
    except Exception as exc:
        show_sheet_error("σύνδεση", exc)
        st.stop()

    sheet_name = st.secrets.get("SHEET_NAME", SHEET_NAME)

    try:
        try:
            sheet = client.open(sheet_name)
        except gspread.SpreadsheetNotFound:
            sheet = client.create(sheet_name)
    except Exception as exc:
        show_sheet_error("άνοιγμα ή δημιουργία Sheet", exc)
        st.stop()

    try:
        try:
            ws = sheet.worksheet(WS_NAME)
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title=WS_NAME, rows=5000, cols=40)
            ws.append_row(COLUMNS)
            return ws

        headers = ws.row_values(1)
        if not headers:
            ws.append_row(COLUMNS)
        else:
            missing = [column for column in COLUMNS if column not in headers]
            if missing:
                ws.update("A1", [headers + missing])
        return ws
    except Exception as exc:
        show_sheet_error("ρύθμιση φύλλου Transactions", exc)
        st.stop()


def load_data():
    try:
        records = worksheet().get_all_records()
    except Exception as exc:
        show_sheet_error("ανάγνωση κινήσεων", exc)
        st.stop()

    df = pd.DataFrame(records)
    for column in COLUMNS:
        if column not in df.columns:
            df[column] = ""

    for column in ["Barcode", "CodeType", "CodeValue", "TransactionId", "VoidOf"]:
        df[column] = df[column].astype(str).fillna("")

    legacy_code = (
        df["CodeValue"].str.strip().eq("")
        & df["Barcode"].str.strip().ne("")
    )
    df.loc[legacy_code, "CodeType"] = "Barcode"
    df.loc[legacy_code, "CodeValue"] = df.loc[legacy_code, "Barcode"]

    legacy_timestamp = df["Timestamp"].astype(str).str.strip().eq("")
    df.loc[legacy_timestamp, "Timestamp"] = df.loc[legacy_timestamp, "Ημερομηνία"]

    df["DeltaQty"] = pd.to_numeric(df["DeltaQty"], errors="coerce").fillna(0)
    df["LocationId"] = pd.to_numeric(
        df["LocationId"], errors="coerce"
    ).fillna(-1).astype(int)
    df["Voided"] = df["Voided"].astype(str).str.lower().isin(
        ["true", "1", "yes", "ναι"]
    )

    return df[COLUMNS]


def add_row(row):
    try:
        worksheet().append_row(
            [row.get(column, "") for column in COLUMNS],
            value_input_option="USER_ENTERED",
        )
    except Exception as exc:
        show_sheet_error("αποθήκευση κίνησης", exc)
        st.stop()


def active_movements(df):
    if df.empty:
        return df.copy()

    active = df.copy()
    active["DeltaQty"] = pd.to_numeric(
        active["DeltaQty"], errors="coerce"
    ).fillna(0)
    active["Voided"] = active["Voided"].astype(bool)
    return active[~active["Voided"]].copy()


def current_stock(df, code_type, code_value, location_id):
    active = active_movements(df)
    if active.empty:
        return 0

    mask = (
        active["CodeType"].astype(str).eq(str(code_type))
        & active["CodeValue"].astype(str).eq(str(code_value))
        & active["LocationId"].eq(int(location_id))
    )
    return int(active.loc[mask, "DeltaQty"].sum())


def to_img(file):
    data = np.frombuffer(file.getvalue(), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def detect_barcode(image):
    try:
        detector = cv2.barcode.BarcodeDetector()
        ok, values, _, _ = detector.detectAndDecode(image)
        if ok and values:
            for value in values:
                barcode = clean_barcode(value)
                if barcode:
                    return barcode
    except Exception:
        pass
    return ""


def detect_qr(image):
    try:
        detector = cv2.QRCodeDetector()
        value, _, _ = detector.detectAndDecode(image)
        return clean(value)
    except Exception:
        return ""


def detect_code(front=None, back=None):
    for image in [back, front]:
        if image is not None:
            barcode = detect_barcode(image)
            if barcode:
                return "Barcode", barcode

    for image in [back, front]:
        if image is not None:
            qr = detect_qr(image)
            if qr:
                return "QR", qr

    return "Barcode", ""


@st.cache_resource(show_spinner=False)
def ocr_reader():
    return easyocr.Reader(["el", "en"], gpu=False)


def detect_product_name(image):
    if image is None:
        return "", []

    try:
        lines = ocr_reader().readtext(image, detail=0)
    except Exception as exc:
        st.warning(f"Το OCR δεν ολοκληρώθηκε: {exc}")
        return "", []

    lines = [clean(line) for line in lines if clean(line)]
    if not lines:
        return "", []

    return max(lines, key=len), lines


def stock_table(df):
    columns = [
        "CodeType",
        "CodeValue",
        "Barcode",
        "Μάρκα",
        "Προϊόν",
        "Κατηγορία",
        "Αποθήκη",
        "Κύριο Κτήριο",
        "Πρώτος Όροφος",
        "Σύνολο",
        "FrontPhotoUrl",
        "BackPhotoUrl",
    ]
    active = active_movements(df)
    if active.empty:
        return pd.DataFrame(columns=columns)

    data = active.copy()
    text_columns = [
        "CodeType",
        "CodeValue",
        "Barcode",
        "Μάρκα",
        "Προϊόν",
        "Κατηγορία",
        "FrontPhotoUrl",
        "BackPhotoUrl",
    ]
    for column in text_columns:
        data[column] = data[column].astype(str).fillna("")

    grouped = (
        data.groupby(
            [
                "CodeType",
                "CodeValue",
                "Barcode",
                "Μάρκα",
                "Προϊόν",
                "Κατηγορία",
                "LocationId",
            ],
            dropna=False,
        )["DeltaQty"]
        .sum()
        .reset_index()
    )

    pivot = grouped.pivot_table(
        index=[
            "CodeType",
            "CodeValue",
            "Barcode",
            "Μάρκα",
            "Προϊόν",
            "Κατηγορία",
        ],
        columns="LocationId",
        values="DeltaQty",
        fill_value=0,
    ).reset_index()

    pivot = pivot.rename(
        columns={
            0: "Αποθήκη",
            1: "Κύριο Κτήριο",
            2: "Πρώτος Όροφος",
        }
    )

    for column in ["Αποθήκη", "Κύριο Κτήριο", "Πρώτος Όροφος"]:
        if column not in pivot.columns:
            pivot[column] = 0

    pivot["Σύνολο"] = (
        pivot["Αποθήκη"]
        + pivot["Κύριο Κτήριο"]
        + pivot["Πρώτος Όροφος"]
    )

    photos = (
        data.sort_values("Timestamp")
        .groupby("CodeValue", dropna=False)[
            ["FrontPhotoUrl", "BackPhotoUrl"]
        ]
        .last()
        .reset_index()
    )

    return (
        pivot.merge(photos, on="CodeValue", how="left")
        .sort_values("Σύνολο", ascending=False)
    )


def search_stock(stock, query):
    query = clean(query).lower()
    if not query or stock.empty:
        return stock, ""

    code_matches = stock[
        stock["CodeValue"].astype(str).str.lower().str.contains(query, na=False)
        | stock["Barcode"].astype(str).str.lower().str.contains(query, na=False)
    ]
    if not code_matches.empty:
        return code_matches, "Βρέθηκε με Barcode / QR"

    text_matches = stock[
        stock["Μάρκα"].astype(str).str.lower().str.contains(query, na=False)
        | stock["Προϊόν"].astype(str).str.lower().str.contains(query, na=False)
        | stock["Κατηγορία"].astype(str).str.lower().str.contains(query, na=False)
    ]
    if not text_matches.empty:
        return text_matches, "Βρέθηκε με Μάρκα / Όνομα / Κατηγορία"

    return stock.iloc[0:0], "Δεν βρέθηκε αποτέλεσμα"


def cards(stock):
    if stock.empty:
        st.info("Δεν υπάρχουν αποτελέσματα.")
        return

    for _, row in stock.head(20).iterrows():
        with st.container(border=True):
            photo_column, details_column = st.columns([1, 2])

            with photo_column:
                front_url = clean(row.get("FrontPhotoUrl", ""))
                if front_url:
                    st.image(
                        front_url,
                        caption="Πρόσοψη",
                        use_container_width=True,
                    )
                else:
                    st.info("Δεν υπάρχει φωτογραφία")

            with details_column:
                st.markdown(f"### {row.get('Προϊόν', '')}")
                st.write(f"**Μάρκα:** {row.get('Μάρκα', '') or '—'}")
                st.write(
                    f"**Κωδικός:** {row.get('CodeType', '')} | "
                    f"`{row.get('CodeValue', '')}`"
                )
                st.write(f"**Αποθήκη:** {int(row.get('Αποθήκη', 0))}")
                st.write(
                    f"**Κύριο Κτήριο:** {int(row.get('Κύριο Κτήριο', 0))}"
                )
                st.write(
                    f"**Πρώτος Όροφος:** "
                    f"{int(row.get('Πρώτος Όροφος', 0))}"
                )
                st.success(f"Σύνολο: {int(row.get('Σύνολο', 0))}")

                back_url = clean(row.get("BackPhotoUrl", ""))
                if back_url:
                    st.image(
                        back_url,
                        caption="Πίσω / Barcode",
                        use_container_width=True,
                    )


def transaction_row(
    code_type,
    code_value,
    barcode,
    brand,
    product,
    category,
    location_id,
    movement,
    quantity,
    delta,
    front_url="",
    back_url="",
    note="",
    void_of="",
):
    now = datetime.now()
    return {
        "TransactionId": str(uuid.uuid4()),
        "Timestamp": now.isoformat(timespec="seconds"),
        "Ημερομηνία": now.strftime("%Y-%m-%d %H:%M"),
        "CodeType": code_type,
        "CodeValue": code_value,
        "Barcode": barcode,
        "Μάρκα": clean(brand),
        "Προϊόν": clean(product),
        "Κατηγορία": category,
        "LocationId": int(location_id),
        "Τοποθεσία": LOCATIONS[int(location_id)],
        "Κίνηση": movement,
        "Ποσότητα": int(quantity),
        "DeltaQty": int(delta),
        "FrontPhotoUrl": clean(front_url),
        "BackPhotoUrl": clean(back_url),
        "Σημείωση": clean(note),
        "Voided": False,
        "VoidOf": clean(void_of),
    }


st.set_page_config(
    page_title="Αποθήκη Φαρμακείου",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Αποθήκη Φαρμακείου")
st.caption(
    "Barcode/QR, αναζήτηση, OCR και ασφαλές ιστορικό κινήσεων στο Google Sheets."
)

entry_tab, search_tab, sales_tab, data_tab, reversal_tab = st.tabs(
    [
        "➕ Καταχώρηση",
        "🔎 Search",
        "📅 Πωλήσεις",
        "📄 Δεδομένα",
        "↩️ Αναστροφή",
    ]
)

with entry_tab:
    st.subheader("Καταχώρηση")

    front_column, back_column = st.columns(2)
    with front_column:
        front_file = st.camera_input(
            "Φωτογραφία πρόσοψης",
            key="front",
        ) or st.file_uploader(
            "Ή ανέβασε πρόσοψη",
            ["jpg", "jpeg", "png"],
            key="front_up",
        )
    with back_column:
        back_file = st.camera_input(
            "Φωτογραφία πίσω / Barcode / QR",
            key="back",
        ) or st.file_uploader(
            "Ή ανέβασε πίσω",
            ["jpg", "jpeg", "png"],
            key="back_up",
        )

    front_image = to_img(front_file) if front_file else None
    back_image = to_img(back_file) if back_file else None

    if front_image is not None:
        st.image(front_image, caption="Πρόσοψη", use_container_width=True)
    if back_image is not None:
        st.image(back_image, caption="Πίσω", use_container_width=True)

    detected_type, detected_code = detect_code(front_image, back_image)
    if detected_code:
        st.success(f"Βρέθηκε {detected_type}: {detected_code}")

    detected_product = ""
    ocr_lines = []
    if front_image is not None:
        with st.spinner("Αναγνώριση ονόματος προϊόντος..."):
            detected_product, ocr_lines = detect_product_name(front_image)
        if detected_product:
            st.info(f"Πρόταση OCR: {detected_product}")
        if ocr_lines:
            with st.expander("Όλα τα αποτελέσματα OCR"):
                st.write(ocr_lines)

    code_type = st.selectbox(
        "Τύπος κωδικού",
        ["Barcode", "QR", "Other"],
        index=["Barcode", "QR", "Other"].index(detected_type),
    )
    code_value_input = st.text_input(
        "Barcode / QR",
        value=detected_code,
    )
    brand = st.text_input("Μάρκα")
    product = st.text_input(
        "Όνομα προϊόντος",
        value=detected_product,
    )
    category = st.selectbox("Κατηγορία", CATEGORIES)
    front_url = st.text_input("URL φωτογραφίας πρόσοψης")
    back_url = st.text_input("URL φωτογραφίας πίσω/barcode")

    location_label = st.selectbox(
        "Τοποθεσία",
        [f"{key} - {value}" for key, value in LOCATIONS.items()],
    )
    location_id = int(location_label.split("-")[0].strip())

    movement = st.selectbox(
        "Κίνηση",
        [
            "Παραλαβή (+)",
            "Πώληση (-)",
            "Διόρθωση (+)",
            "Διόρθωση (-)",
        ],
    )
    quantity = st.number_input(
        "Ποσότητα",
        min_value=1,
        value=1,
        step=1,
    )
    note = st.text_input("Σημείωση")

    if st.button("💾 Αποθήκευση", use_container_width=True):
        final_code = clean(code_value_input)
        if not final_code:
            st.error("Βάλε Barcode ή QR.")
            st.stop()

        barcode = clean_barcode(final_code) if code_type == "Barcode" else ""
        if code_type == "Barcode" and not barcode:
            st.error("Διάλεξες Barcode αλλά δεν έβαλες αριθμητικό barcode.")
            st.stop()

        code_value = barcode if code_type == "Barcode" else final_code

        if not clean(product):
            st.error("Βάλε όνομα προϊόντος.")
            st.stop()

        delta = (
            int(quantity)
            if movement in ["Παραλαβή (+)", "Διόρθωση (+)"]
            else -int(quantity)
        )

        data = load_data()
        existing_stock = current_stock(
            data,
            code_type,
            code_value,
            location_id,
        )
        if delta < 0 and existing_stock + delta < 0:
            st.error(
                f"Η κίνηση θα έκανε το stock αρνητικό. "
                f"Διαθέσιμα: {existing_stock}, ζητήθηκαν: {abs(delta)}."
            )
            st.stop()

        add_row(
            transaction_row(
                code_type=code_type,
                code_value=code_value,
                barcode=barcode,
                brand=brand,
                product=product,
                category=category,
                location_id=location_id,
                movement=movement,
                quantity=quantity,
                delta=delta,
                front_url=front_url,
                back_url=back_url,
                note=note,
            )
        )
        st.success("Αποθηκεύτηκε στο Google Sheets.")

with search_tab:
    st.subheader("Search")
    stock = stock_table(load_data())
    query = st.text_input(
        "Γράψε Barcode, QR, Μάρκα ή Όνομα",
        placeholder="π.χ. Depon ή 520...",
    )
    results, message = search_stock(stock, query)
    if message:
        st.info(message)

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric(
        "Σύνολο",
        int(results["Σύνολο"].sum()) if not results.empty else 0,
    )
    metric_2.metric(
        "Αποθήκη",
        int(results["Αποθήκη"].sum()) if not results.empty else 0,
    )
    metric_3.metric(
        "Κύριο Κτήριο",
        int(results["Κύριο Κτήριο"].sum()) if not results.empty else 0,
    )
    metric_4.metric(
        "Πρώτος Όροφος",
        int(results["Πρώτος Όροφος"].sum()) if not results.empty else 0,
    )

    cards(results)
    with st.expander("Πίνακας"):
        st.dataframe(results, use_container_width=True)

with sales_tab:
    st.subheader("Πωλήσεις")
    data = active_movements(load_data())

    if data.empty:
        st.info("Δεν υπάρχουν δεδομένα.")
    else:
        data["Timestamp_dt"] = pd.to_datetime(
            data["Timestamp"],
            errors="coerce",
        )
        start_column, end_column = st.columns(2)
        start = pd.to_datetime(start_column.date_input("Από"))
        end = pd.to_datetime(end_column.date_input("Έως")) + pd.Timedelta(days=1)

        period = data[
            (data["Timestamp_dt"] >= start)
            & (data["Timestamp_dt"] < end)
        ]
        sales = period[period["DeltaQty"] < 0].copy()

        if sales.empty:
            st.info("Δεν υπάρχουν πωλήσεις ή αφαιρέσεις.")
        else:
            sales["Πωλήθηκαν"] = sales["DeltaQty"].abs()
            report = (
                sales.groupby(
                    [
                        "CodeType",
                        "CodeValue",
                        "Barcode",
                        "Μάρκα",
                        "Προϊόν",
                        "Τοποθεσία",
                    ],
                    dropna=False,
                )["Πωλήθηκαν"]
                .sum()
                .reset_index()
                .sort_values("Πωλήθηκαν", ascending=False)
            )
            st.metric("Σύνολο τεμαχίων", int(report["Πωλήθηκαν"].sum()))
            st.dataframe(report, use_container_width=True)

with data_tab:
    st.subheader("Όλες οι κινήσεις")
    all_data = load_data()
    st.dataframe(all_data, use_container_width=True)

with reversal_tab:
    st.subheader("Αναστροφή λανθασμένης κίνησης")
    st.caption(
        "Δεν διαγράφουμε ιστορικό. Δημιουργούμε αντίθετη κίνηση με σύνδεση "
        "στην αρχική συναλλαγή."
    )

    all_data = load_data()
    reversible = all_data[
        all_data["TransactionId"].astype(str).str.strip().ne("")
        & ~all_data["Voided"].astype(bool)
    ].copy()

    if reversible.empty:
        st.info("Δεν υπάρχουν διαθέσιμες κινήσεις για αναστροφή.")
    else:
        reversed_ids = set(
            all_data.loc[
                all_data["VoidOf"].astype(str).str.strip().ne(""),
                "VoidOf",
            ].astype(str)
        )
        reversible = reversible[
            ~reversible["TransactionId"].astype(str).isin(reversed_ids)
        ]

        options = {
            (
                f"{row['Ημερομηνία']} | {row['Προϊόν']} | "
                f"{row['Τοποθεσία']} | Δ={int(row['DeltaQty'])} | "
                f"{row['TransactionId']}"
            ): index
            for index, row in reversible.iterrows()
        }

        if not options:
            st.info("Όλες οι διαθέσιμες κινήσεις έχουν ήδη αναστραφεί.")
        else:
            label = st.selectbox("Επίλεξε κίνηση", list(options))
            reason = st.text_input("Λόγος αναστροφής")
            confirm = st.checkbox(
                "Επιβεβαιώνω ότι θέλω να δημιουργηθεί αντίθετη κίνηση."
            )

            if st.button("↩️ Δημιουργία αναστροφής", use_container_width=True):
                if not confirm:
                    st.error("Χρειάζεται επιβεβαίωση.")
                    st.stop()

                original = reversible.loc[options[label]]
                reverse_delta = -int(original["DeltaQty"])
                stock_now = current_stock(
                    all_data,
                    original["CodeType"],
                    original["CodeValue"],
                    int(original["LocationId"]),
                )
                if reverse_delta < 0 and stock_now + reverse_delta < 0:
                    st.error(
                        "Η αναστροφή θα έκανε το stock αρνητικό και "
                        "δεν επιτρέπεται."
                    )
                    st.stop()

                movement = (
                    "Αναστροφή (+)"
                    if reverse_delta > 0
                    else "Αναστροφή (-)"
                )
                add_row(
                    transaction_row(
                        code_type=original["CodeType"],
                        code_value=original["CodeValue"],
                        barcode=original["Barcode"],
                        brand=original["Μάρκα"],
                        product=original["Προϊόν"],
                        category=original["Κατηγορία"],
                        location_id=int(original["LocationId"]),
                        movement=movement,
                        quantity=abs(reverse_delta),
                        delta=reverse_delta,
                        front_url=original["FrontPhotoUrl"],
                        back_url=original["BackPhotoUrl"],
                        note=(
                            f"Αναστροφή {original['TransactionId']}. "
                            f"{clean(reason)}"
                        ),
                        void_of=original["TransactionId"],
                    )
                )
                st.success("Η αναστροφή καταχωρήθηκε χωρίς διαγραφή ιστορικού.")
