# Αποθήκη Φαρμακείου

Το κύριο MVP entrypoint είναι το `app_inventory_search.py`.

## Λειτουργίες

- Καταχώρηση παραλαβών, πωλήσεων και διορθώσεων
- Barcode / QR detection
- Προαιρετικό OCR για πρόταση ονόματος προϊόντος
- Stock ανά τοποθεσία
- Αναζήτηση προϊόντων
- Αναφορές πωλήσεων ανά περίοδο
- Προστασία από αρνητικό stock με fresh read πριν την εγγραφή
- Αυτόματη αντιστάθμιση αν μια ταυτόχρονη εγγραφή προκαλέσει αρνητικό stock
- Μοναδικά transaction IDs και timestamps
- Ασφαλής αναστροφή λανθασμένων κινήσεων χωρίς διαγραφή ιστορικού
- Google Sheets ως βασικό storage

## Μοντέλο κινήσεων

Η εφαρμογή χρησιμοποιεί **compensating ledger**:

- Οι αρχικές κινήσεις παραμένουν στο ιστορικό.
- Οι αναστροφές γράφονται ως νέες αντίθετες κινήσεις.
- Το `VoidOf` συνδέει την αναστροφή με την αρχική κίνηση.
- Το `MovementKind` ξεχωρίζει `Normal`, `Reversal` και `Compensation`.
- Το `Voided` χρησιμοποιείται μόνο ως παλιό/manual exclusion πεδίο και όχι για κανονικές αναστροφές.

## Περιορισμός ταυτόχρονης χρήσης

Το Google Sheets **δεν είναι transactional database**. Η εφαρμογή ξαναδιαβάζει το stock πριν από αφαιρετικές κινήσεις, ελέγχει ξανά μετά την εγγραφή και δημιουργεί αντισταθμιστική κίνηση όταν εντοπίζεται race condition. Αυτή είναι best-effort προστασία, όχι απόλυτο lock.

Για έντονη ταυτόχρονη χρήση από πολλούς χρήστες, οι εγγραφές πρέπει να μεταφερθούν πίσω από Google Apps Script `LockService` ή σε transactional database.

## Φωτογραφίες

Η εφαρμογή δέχεται φωτογραφίες μόνο για άμεσο barcode/OCR έλεγχο. Μόνιμη αποθήκευση φωτογραφιών δεν έχει υλοποιηθεί ακόμη και αυθαίρετα εξωτερικά image URLs δεν αποθηκεύονται ή προβάλλονται.

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
3. Δημιούργησε το Google Sheet και ένα worksheet με όνομα `Transactions`.
4. Μοιράσου το Google Sheet με το `client_email` του service account.
5. Προαιρετικά άλλαξε το `SHEET_NAME`.

Η εφαρμογή χρησιμοποιεί μόνο το scope `https://www.googleapis.com/auth/spreadsheets` και δεν δημιουργεί αρχεία στο Google Drive.

Μην ανεβάσεις το πραγματικό `.streamlit/secrets.toml` στο GitHub.

## Εκτέλεση

```bash
streamlit run app_inventory_search.py
```

Η εφαρμογή ανοίγει συνήθως στο `http://localhost:8501`.

## Tests

```bash
pytest -q
```
