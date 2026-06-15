# Αποθήκη Φαρμακείου

Κύριο MVP: `app_inventory_search.py`.

## Λειτουργίες

- Barcode και QR detection
- Fallback με `PCCode` και `SerialNumber` όταν δεν διαβάζεται το QR
- Τα PC και SN είναι προαιρετικά μεμονωμένα: αρκεί να υπάρχει ένα από τα δύο
- Αναζήτηση με Barcode, QR, PC, SN, μάρκα ή όνομα
- Προαιρετικό OCR
- Stock ανά τοποθεσία
- Αναφορές πωλήσεων
- Προστασία από αρνητικό stock
- Ασφαλείς αναστροφές χωρίς διαγραφή ιστορικού

## QR fallback

Όταν δεν διαβάζεται το QR, άφησε κενό το βασικό πεδίο κωδικού και συμπλήρωσε PC και/ή SN. Αν υπάρχουν και τα δύο, αποθηκεύονται σε ξεχωριστές στήλες.

## Μοντέλο κινήσεων

Η εφαρμογή χρησιμοποιεί compensating ledger. Οι αρχικές κινήσεις παραμένουν, οι αναστροφές γράφονται ως νέες αντίθετες κινήσεις, το `VoidOf` συνδέει τις εγγραφές και το `MovementKind` ξεχωρίζει normal, reversal και compensation.

## Ταυτόχρονη χρήση

Το Google Sheets δεν είναι transactional database. Η εφαρμογή κάνει fresh read πριν από αφαιρετικές κινήσεις, επανέλεγχο μετά την εγγραφή και αυτόματη αντιστάθμιση όταν εντοπίζεται race condition. Για έντονη ταυτόχρονη χρήση χρειάζεται αργότερα LockService ή transactional database.

## Φωτογραφίες

Οι φωτογραφίες χρησιμοποιούνται μόνο για άμεσο barcode/OCR έλεγχο. Δεν αποθηκεύονται μόνιμα και δεν φορτώνονται εξωτερικά image URLs.

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

Δημιούργησε το Google Sheet και worksheet με όνομα `Transactions`, συμπλήρωσε το τοπικό Streamlit secrets αρχείο και μοιράσου το Sheet με το service-account email. Η εφαρμογή χρησιμοποιεί μόνο spreadsheet scope και δεν δημιουργεί αρχεία στο Drive.

## Εκτέλεση

```bash
streamlit run app_inventory_search.py
```

## Tests

Από τη ρίζα του repository:

```bash
python -m pytest -q
```
