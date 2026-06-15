# Αποθήκη Φαρμακείου

Το κύριο MVP entrypoint είναι το `app_inventory_search.py`.

## Λειτουργίες

- Καταχώρηση παραλαβών, πωλήσεων και διορθώσεων
- Barcode / QR detection
- OCR για πρόταση ονόματος προϊόντος
- Stock ανά τοποθεσία
- Αναζήτηση προϊόντων
- Αναφορές πωλήσεων ανά περίοδο
- Προστασία από αρνητικό stock
- Μοναδικά transaction IDs και timestamps
- Ασφαλής αναστροφή λανθασμένων κινήσεων χωρίς διαγραφή ιστορικού
- Google Sheets ως βασικό storage

## Εγκατάσταση

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Σε Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Ρύθμιση Google Sheets

1. Αντέγραψε το `.streamlit/secrets.toml.example` σε `.streamlit/secrets.toml`.
2. Συμπλήρωσε τα πραγματικά στοιχεία του Google service account.
3. Μοιράσου το Google Sheet με το `client_email` του service account.
4. Προαιρετικά άλλαξε το `SHEET_NAME`.

Μην ανεβάσεις το πραγματικό `.streamlit/secrets.toml` στο GitHub.

## Εκτέλεση

```bash
streamlit run app_inventory_search.py
```

Η εφαρμογή ανοίγει συνήθως στο `http://localhost:8501`.
