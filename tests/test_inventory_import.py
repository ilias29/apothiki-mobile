import io
import unittest

import pandas as pd

import inventory_import as inventory_io


def csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")


class InventoryImportTests(unittest.TestCase):
    def test_reads_greek_chatgpt_csv(self):
        raw = csv_bytes(
            pd.DataFrame(
                [
                    {
                        "Προϊόν": "Depon 500mg",
                        "Ποσότητα": 4,
                        "Περιεκτικότητα": "500 mg",
                        "Λήξη": "12/2027",
                    }
                ]
            )
        )
        result = inventory_io.read_inventory_bytes(raw, "stock.csv")
        self.assertEqual(result.loc[0, "ProductName"], "DEPON 500MG")
        self.assertEqual(result.loc[0, "EstimatedQty"], 4)
        self.assertEqual(result.loc[0, "Strength"], "500 mg")
        self.assertEqual(result.loc[0, "ExpiryDate"], "12/2027")
        self.assertFalse(bool(result.loc[0, "confirm"]))

    def test_accepts_quantity_page_total_quantity_column(self):
        raw = csv_bytes(pd.DataFrame([{"ProductName": "Briviact", "TotalQuantity": 7}]))
        result = inventory_io.read_inventory_bytes(raw, "photo_count.csv")
        self.assertEqual(result.loc[0, "EstimatedQty"], 7)

    def test_reads_xlsx(self):
        buffer = io.BytesIO()
        pd.DataFrame([{"ProductName": "Aerius", "EstimatedQty": 2}]).to_excel(buffer, index=False)
        result = inventory_io.read_inventory_bytes(buffer.getvalue(), "stock.xlsx")
        self.assertEqual(result.loc[0, "ProductName"], "AERIUS")
        self.assertEqual(result.loc[0, "EstimatedQty"], 2)

    def test_rejects_unsafe_quantities(self):
        for quantity in [0, -1, 1.5, "abc"]:
            with self.subTest(quantity=quantity):
                raw = csv_bytes(pd.DataFrame([{"ProductName": "Depon", "EstimatedQty": quantity}]))
                with self.assertRaisesRegex(ValueError, "ποσότητα"):
                    inventory_io.read_inventory_bytes(raw, "stock.csv")

    def test_requires_product_and_quantity_columns(self):
        raw = csv_bytes(pd.DataFrame([{"Notes": "nothing useful"}]))
        with self.assertRaisesRegex(ValueError, "στήλες προϊόντος και ποσότητας"):
            inventory_io.read_inventory_bytes(raw, "stock.csv")

    def test_transaction_ids_are_stable_and_row_specific(self):
        raw = b"same file"
        batch = inventory_io.import_batch_id(raw)
        self.assertEqual(inventory_io.transaction_id(batch, 1), inventory_io.transaction_id(batch, 1))
        self.assertNotEqual(inventory_io.transaction_id(batch, 1), inventory_io.transaction_id(batch, 2))

    def test_duplicate_rows_include_expiry_and_lot_in_identity(self):
        frame = inventory_io.normalize_draft_columns(
            pd.DataFrame(
                [
                    {"ProductName": "Depon", "EstimatedQty": 2, "ExpiryDate": "2027-01-31", "LotNumber": "A"},
                    {"ProductName": "Depon", "EstimatedQty": 3, "ExpiryDate": "2027-01-31", "LotNumber": "A"},
                    {"ProductName": "Depon", "EstimatedQty": 1, "ExpiryDate": "2028-01-31", "LotNumber": "B"},
                ]
            )
        )
        self.assertEqual(len(inventory_io.duplicate_rows(frame)), 2)


if __name__ == "__main__":
    unittest.main()
