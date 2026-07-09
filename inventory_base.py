import hashlib
from datetime import datetime
from typing import Any

import pandas as pd

PRODUCT_COLUMNS = [
    "ProductId",
    "Barcode",
    "GTIN",
    "PC_GTIN",
    "DataMatrix_PC",
    "DataMatrix_SN",
    "ProductName",
    "Brand",
    "Category",
    "Strength",
    "DosageForm",
    "Company",
    "FrontPhotoUrl",
    "BackPhotoUrl",
    "Active",
    "CreatedAt",
    "UpdatedAt",
    "Notes",
]

SUPPLIER_MAPPING_COLUMNS = [
    "MappingId",
    "SupplierName",
    "SupplierItemCode",
    "SupplierDescription",
    "Barcode",
    "GTIN",
    "ProductName",
    "Brand",
    "Strength",
    "DosageForm",
    "Confirmed",
    "LastConfirmedAt",
    "Confidence",
    "Notes",
]

INVOICE_LINE_COLUMNS = [
    "InvoiceLineId",
    "SupplierName",
    "DocumentType",
    "DocumentNumber",
    "DocumentDate",
    "LineNumber",
    "SupplierItemCode",
    "RawDescription",
    "Quantity",
    "UnitPrice",
    "DiscountPercent",
    "NetValue",
    "VAT",
    "MatchedBarcode",
    "MatchedGTIN",
    "MatchedProductName",
    "LocationId",
    "LocationName",
    "Confirmed",
    "ImportedToStock",
    "ImportedAt",
    "Notes",
]

SALES_LINE_COLUMNS = [
    "SalesLineId",
    "SaleDate",
    "RawCode",
    "RawDescription",
    "QuantitySold",
    "MatchedBarcode",
    "MatchedGTIN",
    "MatchedProductName",
    "LocationId",
    "LocationName",
    "Confirmed",
    "ImportedToStock",
    "ImportedAt",
    "Notes",
]

SHEET_SCHEMAS = {
    "Products": PRODUCT_COLUMNS,
    "SupplierMappings": SUPPLIER_MAPPING_COLUMNS,
    "InvoiceLines": INVOICE_LINE_COLUMNS,
    "SalesLines": SALES_LINE_COLUMNS,
}


def clean(value: Any) -> str:
    return str(value or "").strip()


def up(value: Any) -> str:
    return clean(value).upper()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stable_hash(*parts: Any, size: int = 16) -> str:
    raw = "|".join(clean(part).lower() for part in parts if clean(part)) or "blank"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def product_id(barcode: str = "", gtin: str = "", product_name: str = "", strength: str = "", dosage_form: str = "", pc_gtin: str = "") -> str:
    return "prd_" + stable_hash(barcode, gtin, pc_gtin, product_name, strength, dosage_form)


def mapping_id(supplier_name: str, supplier_item_code: str, supplier_description: str = "") -> str:
    return "map_" + stable_hash(supplier_name, supplier_item_code, supplier_description)


def workbook(core):
    return core.worksheet().spreadsheet


def ensure_worksheet(core, sheet_name: str, columns: list[str]):
    book = workbook(core)
    try:
        ws = book.worksheet(sheet_name)
    except Exception:
        ws = book.add_worksheet(title=sheet_name, rows=1000, cols=max(len(columns), 10))
        ws.update("A1", [columns])
        return ws

    headers = [clean(h) for h in ws.row_values(1)]
    if not headers or not any(headers):
        ws.update("A1", [columns])
        return ws
    missing = [column for column in columns if column not in headers]
    if missing:
        ws.update("A1", [headers + missing])
    return ws


def ensure_base_sheets(core) -> dict[str, int]:
    sizes = {}
    for sheet_name, columns in SHEET_SCHEMAS.items():
        ws = ensure_worksheet(core, sheet_name, columns)
        sizes[sheet_name] = len(ws.get_all_records())
    return sizes


def read_sheet_df(core, sheet_name: str, columns: list[str]) -> pd.DataFrame:
    try:
        ws = workbook(core).worksheet(sheet_name)
    except Exception:
        return pd.DataFrame(columns=columns)
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    return df[columns]


def infer_pc_gtin(row: dict[str, Any]) -> str:
    return clean(row.get("PC_GTIN", "")) or clean(row.get("PCCode", "")) or clean(row.get("DataMatrix_PC", ""))


def infer_datamatrix_sn(row: dict[str, Any]) -> str:
    return clean(row.get("DataMatrix_SN", "")) or clean(row.get("SerialNumber", ""))


def product_rows_from_transactions(data: pd.DataFrame) -> list[dict[str, str]]:
    if data is None or data.empty:
        return []
    df = data.copy(deep=True)
    for column in ["Barcode", "GTIN", "PCCode", "DataMatrix_PC", "SerialNumber", "DataMatrix_SN", "Προϊόν", "Μάρκα", "Κατηγορία", "Strength", "DosageForm", "FrontPhotoUrl", "BackPhotoUrl"]:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)

    rows: dict[str, dict[str, str]] = {}
    stamp = now_iso()
    for _, item in df.iterrows():
        product_name = up(item.get("Προϊόν", ""))
        barcode = clean(item.get("Barcode", ""))
        gtin = clean(item.get("GTIN", ""))
        pc_gtin = infer_pc_gtin(item)
        datamatrix_sn = infer_datamatrix_sn(item)
        strength = up(item.get("Strength", ""))
        dosage_form = up(item.get("DosageForm", ""))
        if not product_name and not barcode and not gtin and not pc_gtin:
            continue
        pid = product_id(barcode, gtin, product_name, strength, dosage_form, pc_gtin)
        rows[pid] = {
            "ProductId": pid,
            "Barcode": barcode,
            "GTIN": gtin,
            "PC_GTIN": pc_gtin,
            "DataMatrix_PC": pc_gtin,
            "DataMatrix_SN": datamatrix_sn,
            "ProductName": product_name,
            "Brand": up(item.get("Μάρκα", "")),
            "Category": clean(item.get("Κατηγορία", "")),
            "Strength": strength,
            "DosageForm": dosage_form,
            "Company": "",
            "FrontPhotoUrl": clean(item.get("FrontPhotoUrl", "")),
            "BackPhotoUrl": clean(item.get("BackPhotoUrl", "")),
            "Active": "true",
            "CreatedAt": stamp,
            "UpdatedAt": stamp,
            "Notes": "synced_from_transactions; identifiers=barcode_gtin_pc_sn_photos",
        }
    return sorted(rows.values(), key=lambda row: (row["ProductName"], row["Barcode"], row["GTIN"], row["PC_GTIN"]))


def transaction_row_to_product(row: dict[str, Any]) -> dict[str, str]:
    product_name = up(row.get("Προϊόν", ""))
    barcode = clean(row.get("Barcode", ""))
    gtin = clean(row.get("GTIN", ""))
    pc_gtin = infer_pc_gtin(row)
    datamatrix_sn = infer_datamatrix_sn(row)
    strength = up(row.get("Strength", ""))
    dosage_form = up(row.get("DosageForm", ""))
    stamp = now_iso()
    pid = product_id(barcode, gtin, product_name, strength, dosage_form, pc_gtin)
    return {
        "ProductId": pid,
        "Barcode": barcode,
        "GTIN": gtin,
        "PC_GTIN": pc_gtin,
        "DataMatrix_PC": pc_gtin,
        "DataMatrix_SN": datamatrix_sn,
        "ProductName": product_name,
        "Brand": up(row.get("Μάρκα", "")),
        "Category": clean(row.get("Κατηγορία", "")),
        "Strength": strength,
        "DosageForm": dosage_form,
        "Company": "",
        "FrontPhotoUrl": clean(row.get("FrontPhotoUrl", "")),
        "BackPhotoUrl": clean(row.get("BackPhotoUrl", "")),
        "Active": "true",
        "CreatedAt": stamp,
        "UpdatedAt": stamp,
        "Notes": "created_from_entry; identifiers=barcode_gtin_pc_sn_photos",
    }


def sync_products_from_transactions(core, data: pd.DataFrame) -> dict[str, int]:
    ws = ensure_worksheet(core, "Products", PRODUCT_COLUMNS)
    existing = pd.DataFrame(ws.get_all_records())
    existing_ids = set(existing.get("ProductId", pd.Series(dtype=str)).astype(str)) if not existing.empty else set()
    candidates = product_rows_from_transactions(data)
    new_rows = [row for row in candidates if row["ProductId"] not in existing_ids]
    for row in new_rows:
        ws.append_row([row.get(column, "") for column in PRODUCT_COLUMNS], value_input_option="RAW")
    return {"added": len(new_rows), "candidates": len(candidates), "existing": len(existing_ids)}


def upsert_product_from_transaction(core, row: dict[str, Any]) -> bool:
    product_row = transaction_row_to_product(row)
    if not clean(product_row.get("ProductName")) and not clean(product_row.get("Barcode")) and not clean(product_row.get("GTIN")) and not clean(product_row.get("PC_GTIN")):
        return False
    ws = ensure_worksheet(core, "Products", PRODUCT_COLUMNS)
    existing = pd.DataFrame(ws.get_all_records())
    if not existing.empty and "ProductId" in existing.columns:
        if product_row["ProductId"] in set(existing["ProductId"].astype(str)):
            return False
    ws.append_row([product_row.get(column, "") for column in PRODUCT_COLUMNS], value_input_option="RAW")
    return True


def schema_overview_df() -> pd.DataFrame:
    rows = []
    for sheet_name, columns in SHEET_SCHEMAS.items():
        rows.append({"Φύλλο": sheet_name, "Στήλες": ", ".join(columns)})
    return pd.DataFrame(rows)
