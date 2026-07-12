import pandas as pd

import inventory_base as base


class FakeWorksheet:
    def __init__(self, title, columns):
        self.title = title
        self.headers = list(columns)
        self.records = []

    def row_values(self, row):
        return list(self.headers)

    def get_all_records(self):
        return [dict(record) for record in self.records]

    def append_row(self, values, value_input_option=None):
        self.records.append(dict(zip(self.headers, values)))

    def update(self, cell_range, values):
        if cell_range == "A1":
            self.headers = list(values[0])
            for record in self.records:
                for header in self.headers:
                    record.setdefault(header, "")
            return
        row_number = int(cell_range.split(":", 1)[0][1:])
        self.records[row_number - 2] = dict(zip(self.headers, values[0]))


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {}

    def worksheet(self, name):
        if name not in self.sheets:
            raise KeyError(name)
        return self.sheets[name]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title, base.SHEET_SCHEMAS.get(title, []))
        self.sheets[title] = ws
        return ws


class TransactionsWorksheet:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet


class FakeCore:
    def __init__(self):
        self.book = FakeSpreadsheet()
        self.transactions = TransactionsWorksheet(self.book)

    def worksheet(self):
        return self.transactions


def tx(**overrides):
    row = {
        "TransactionId": "tx-1",
        "Προϊόν": "BUDECOL 3MG",
        "Μάρκα": "BUDECOL",
        "Κατηγορία": "Φάρμακο",
        "Strength": "3MG",
        "DosageForm": "",
        "Barcode": "",
        "GTIN": "",
        "PCCode": "",
        "SerialNumber": "",
        "LotNumber": "",
        "ExpiryDate": "",
        "LocationId": 2,
        "FrontPhotoUrl": "",
        "BackPhotoUrl": "",
    }
    row.update(overrides)
    return row


def test_product_id_ignores_later_identifiers_for_named_product():
    first = base.product_id(product_name="BUDECOL 3MG", strength="3MG")
    later = base.product_id(product_name="BUDECOL 3MG", strength="3MG", pc_gtin="07640129622818", barcode="123")
    assert first == later


def test_name_only_then_pc_updates_same_product_row():
    core = FakeCore()
    base.ensure_base_sheets(core)
    assert base.upsert_product_from_transaction(core, tx()) is True
    assert base.upsert_product_from_transaction(core, tx(TransactionId="tx-2", PCCode="07640129622818")) is True
    products = core.book.worksheet("Products").records
    assert len(products) == 1
    assert products[0]["PC_GTIN"] == "07640129622818"
    assert products[0]["DataMatrix_PC"] == "07640129622818"


def test_same_pc_different_serial_numbers_one_product_two_packages():
    core = FakeCore()
    base.ensure_base_sheets(core)
    base.upsert_product_from_transaction(core, tx(TransactionId="tx-a", PCCode="07640129622818", SerialNumber="SN-A"))
    base.upsert_product_from_transaction(core, tx(TransactionId="tx-b", PCCode="07640129622818", SerialNumber="SN-B"))
    assert len(core.book.worksheet("Products").records) == 1
    packages = core.book.worksheet("PackageIdentifiers").records
    assert len(packages) == 2
    assert {row["SerialNumber"] for row in packages} == {"SN-A", "SN-B"}


def test_same_barcode_different_lot_and_expiry_one_product():
    core = FakeCore()
    base.ensure_base_sheets(core)
    base.upsert_product_from_transaction(core, tx(TransactionId="tx-a", Barcode="5201234567890", LotNumber="A", ExpiryDate="2027-01-31"))
    base.upsert_product_from_transaction(core, tx(TransactionId="tx-b", Barcode="5201234567890", LotNumber="B", ExpiryDate="2028-02-29"))
    assert len(core.book.worksheet("Products").records) == 1


def test_product_rows_from_transactions_deduplicates_identifiers_added_later():
    frame = pd.DataFrame([
        tx(TransactionId="tx-a"),
        tx(TransactionId="tx-b", PCCode="07640129622818", SerialNumber="SN-B"),
    ])
    rows = base.product_rows_from_transactions(frame)
    assert len(rows) == 1
    assert rows[0]["PC_GTIN"] == "07640129622818"


def test_sync_reports_updates_and_packages():
    core = FakeCore()
    base.ensure_base_sheets(core)
    base.upsert_product_from_transaction(core, tx())
    frame = pd.DataFrame([tx(TransactionId="tx-b", PCCode="07640129622818", SerialNumber="SN-B")])
    result = base.sync_products_from_transactions(core, frame)
    assert result["added"] == 0
    assert result["updated"] == 1
    assert result["packages_added"] == 1
    assert len(core.book.worksheet("Products").records) == 1
