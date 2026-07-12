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

PACKAGE_IDENTIFIER_COLUMNS = [
    "PackageIdentifierId",
    "ProductId",
    "PC_GTIN",
    "SerialNumber",
    "LotNumber",
    "ExpiryDate",
    "TransactionId",
    "LocationId",
    "CreatedAt",
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
    "PackageIdentifiers": PACKAGE_IDENTIFIER_COLUMNS,
    "SupplierMappings": SUPPLIER_MAPPING_COLUMNS,
    "InvoiceLines": INVOICE_LINE_COLUMNS,
    "SalesLines": SALES_LINE_COLUMNS,
}


def clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def up(value: Any) -> str:
    return " ".join(clean(value).upper().split())


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stable_hash(*parts: Any, size: int = 16) -> str:
    raw = "|".join(clean(part).lower() for part in parts if clean(part)) or "blank"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def normalized_product_key(product_name: str = "", strength: str = "", dosage_form: str = "") -> tuple[str, str, str]:
    return up(product_name), up(strength), up(dosage_form)


def product_id(
    barcode: str = "",
    gtin: str = "",
    product_name: str = "",
    strength: str = "",
    dosage_form: str = "",
    pc_gtin: str = "",
) -> str:
    """Create an initial internal id that does not change when identifiers are added later.

    Named products use name + strength + dosage form. Identifiers are only a fallback
    when no product name exists. Existing ProductId values are always preserved on update.
    """
    name_key = normalized_product_key(product_name, strength, dosage_form)
    if name_key[0]:
        seed = ("name", *name_key)
    else:
        seed = ("identifier", clean(gtin) or clean(barcode) or clean(pc_gtin) or "unknown")
    return "prd_" + stable_hash(*seed)


def mapping_id(supplier_name: str, supplier_item_code: str, supplier_description: str = "") -> str:
    return "map_" + stable_hash(supplier_name, supplier_item_code, supplier_description)


def package_identifier_id(product_id_value: str, serial_number: str = "", transaction_id: str = "", pc_gtin: str = "", lot_number: str = "", expiry_date: str = "") -> str:
    seed = clean(serial_number) or clean(transaction_id) or stable_hash(pc_gtin, lot_number, expiry_date)
    return "pkg_" + stable_hash(product_id_value, seed)


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


def infer_pc_gtin(row: Any) -> str:
    getter = row.get if hasattr(row, "get") else lambda key, default="": default
    return clean(getter("PC_GTIN", "")) or clean(getter("PCCode", "")) or clean(getter("DataMatrix_PC", ""))


def infer_datamatrix_sn(row: Any) -> str:
    getter = row.get if hasattr(row, "get") else lambda key, default="": default
    return clean(getter("DataMatrix_SN", "")) or clean(getter("SerialNumber", ""))


def identifier_set(row: Any) -> set[str]:
    getter = row.get if hasattr(row, "get") else lambda key, default="": default
    values = {
        clean(getter("Barcode", "")),
        clean(getter("GTIN", "")),
        infer_pc_gtin(row),
        clean(getter("DataMatrix_PC", "")),
    }
    return {value for value in values if value}


def product_names_compatible(left: Any, right: Any) -> bool:
    left_name, left_strength, left_form = normalized_product_key(
        left.get("ProductName", ""), left.get("Strength", ""), left.get("DosageForm", "")
    )
    right_name, right_strength, right_form = normalized_product_key(
        right.get("ProductName", ""), right.get("Strength", ""), right.get("DosageForm", "")
    )
    if not left_name or left_name != right_name:
        return False
    if left_strength and right_strength and left_strength != right_strength:
        return False
    if left_form and right_form and left_form != right_form:
        return False
    return True


def find_matching_product_index(existing: pd.DataFrame, candidate: dict[str, str]) -> int | None:
    if existing is None or existing.empty:
        return None
    candidate_ids = identifier_set(candidate)
    for index, row in existing.iterrows():
        if candidate_ids and candidate_ids.intersection(identifier_set(row)):
            return int(index)
    for index, row in existing.iterrows():
        if product_names_compatible(row, candidate):
            return int(index)
    return None


def transaction_row_to_product(row: dict[str, Any]) -> dict[str, str]:
    product_name = up(row.get("Προϊόν", row.get("ProductName", "")))
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
        "Brand": up(row.get("Μάρκα", row.get("Brand", ""))),
        "Category": clean(row.get("Κατηγορία", row.get("Category", ""))),
        "Strength": strength,
        "DosageForm": dosage_form,
        "Company": clean(row.get("Company", "")),
        "FrontPhotoUrl": clean(row.get("FrontPhotoUrl", "")),
        "BackPhotoUrl": clean(row.get("BackPhotoUrl", "")),
        "Active": "true",
        "CreatedAt": stamp,
        "UpdatedAt": stamp,
        "Notes": "created_from_transaction; serial_is_package_level",
    }


def merge_product_rows(existing_row: dict[str, Any], candidate: dict[str, Any]) -> tuple[dict[str, str], bool]:
    merged = {column: clean(existing_row.get(column, "")) for column in PRODUCT_COLUMNS}
    changed = False
    existing_id = clean(merged.get("ProductId"))
    if not existing_id:
        merged["ProductId"] = clean(candidate.get("ProductId"))
        changed = True
    for column in PRODUCT_COLUMNS:
        if column in {"ProductId", "CreatedAt", "UpdatedAt", "Notes"}:
            continue
        current = clean(merged.get(column, ""))
        incoming = clean(candidate.get(column, ""))
        if not current and incoming:
            merged[column] = incoming
            changed = True
    pc_value = clean(merged.get("PC_GTIN")) or clean(merged.get("DataMatrix_PC")) or clean(candidate.get("PC_GTIN")) or clean(candidate.get("DataMatrix_PC"))
    if pc_value:
        if clean(merged.get("PC_GTIN")) != pc_value:
            merged["PC_GTIN"] = pc_value
            changed = True
        if clean(merged.get("DataMatrix_PC")) != pc_value:
            merged["DataMatrix_PC"] = pc_value
            changed = True
    if not clean(merged.get("CreatedAt")):
        merged["CreatedAt"] = clean(candidate.get("CreatedAt")) or now_iso()
        changed = True
    if changed:
        merged["UpdatedAt"] = now_iso()
        old_note = clean(existing_row.get("Notes", ""))
        marker = "updated_missing_identifiers"
        merged["Notes"] = "; ".join(part for part in [old_note, marker] if part)
    else:
        merged["UpdatedAt"] = clean(existing_row.get("UpdatedAt", ""))
        merged["Notes"] = clean(existing_row.get("Notes", ""))
    return merged, changed


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _write_product_row(ws, row_number: int, row: dict[str, Any]) -> None:
    end_column = _column_letter(len(PRODUCT_COLUMNS))
    ws.update(f"A{row_number}:{end_column}{row_number}", [[row.get(column, "") for column in PRODUCT_COLUMNS]])


def product_rows_from_transactions(data: pd.DataFrame) -> list[dict[str, str]]:
    if data is None or data.empty:
        return []
    rows: list[dict[str, str]] = []
    frame = data.copy(deep=True)
    for _, item in frame.iterrows():
        candidate = transaction_row_to_product(item.to_dict())
        if not candidate["ProductName"] and not identifier_set(candidate):
            continue
        existing = pd.DataFrame(rows, columns=PRODUCT_COLUMNS)
        match_index = find_matching_product_index(existing, candidate)
        if match_index is None:
            rows.append(candidate)
        else:
            merged, _ = merge_product_rows(rows[match_index], candidate)
            rows[match_index] = merged
    return sorted(rows, key=lambda row: (row["ProductName"], row["Strength"], row["DosageForm"]))


def package_row_from_transaction(row: dict[str, Any], product_id_value: str) -> dict[str, str] | None:
    pc_gtin = infer_pc_gtin(row)
    serial_number = infer_datamatrix_sn(row)
    transaction_id = clean(row.get("TransactionId", ""))
    lot_number = clean(row.get("LotNumber", ""))
    expiry_date = clean(row.get("ExpiryDate", ""))
    if not serial_number and not pc_gtin:
        return None
    stamp = now_iso()
    return {
        "PackageIdentifierId": package_identifier_id(product_id_value, serial_number, transaction_id, pc_gtin, lot_number, expiry_date),
        "ProductId": product_id_value,
        "PC_GTIN": pc_gtin,
        "SerialNumber": serial_number,
        "LotNumber": lot_number,
        "ExpiryDate": expiry_date,
        "TransactionId": transaction_id,
        "LocationId": clean(row.get("LocationId", "")),
        "CreatedAt": stamp,
        "Notes": "package_traceability; serial_not_product_identity",
    }


def _upsert_product_in_memory(existing: pd.DataFrame, candidate: dict[str, str]) -> tuple[pd.DataFrame, int, bool, bool]:
    match_index = find_matching_product_index(existing, candidate)
    if match_index is None:
        result = pd.concat([existing, pd.DataFrame([candidate], columns=PRODUCT_COLUMNS)], ignore_index=True)
        return result, len(result) - 1, True, False
    merged, changed = merge_product_rows(existing.loc[match_index].to_dict(), candidate)
    result = existing.copy(deep=True)
    for column in PRODUCT_COLUMNS:
        result.at[match_index, column] = merged.get(column, "")
    return result, match_index, False, changed


def upsert_package_identifier_from_transaction(core, row: dict[str, Any], product_id_value: str) -> bool:
    package_row = package_row_from_transaction(row, product_id_value)
    if not package_row:
        return False
    ws = ensure_worksheet(core, "PackageIdentifiers", PACKAGE_IDENTIFIER_COLUMNS)
    existing = pd.DataFrame(ws.get_all_records())
    if not existing.empty and "PackageIdentifierId" in existing.columns:
        if package_row["PackageIdentifierId"] in set(existing["PackageIdentifierId"].astype(str)):
            return False
    ws.append_row([package_row.get(column, "") for column in PACKAGE_IDENTIFIER_COLUMNS], value_input_option="RAW")
    return True


def upsert_product_from_transaction(core, row: dict[str, Any]) -> bool:
    candidate = transaction_row_to_product(row)
    if not candidate["ProductName"] and not identifier_set(candidate):
        return False
    ws = ensure_worksheet(core, "Products", PRODUCT_COLUMNS)
    existing = pd.DataFrame(ws.get_all_records())
    for column in PRODUCT_COLUMNS:
        if column not in existing.columns:
            existing[column] = ""
    existing = existing[PRODUCT_COLUMNS]
    updated, index, created, changed = _upsert_product_in_memory(existing, candidate)
    product_row = updated.loc[index].to_dict()
    if created:
        ws.append_row([product_row.get(column, "") for column in PRODUCT_COLUMNS], value_input_option="RAW")
    elif changed:
        _write_product_row(ws, index + 2, product_row)
    upsert_package_identifier_from_transaction(core, row, clean(product_row.get("ProductId")))
    return created or changed


def sync_products_from_transactions(core, data: pd.DataFrame) -> dict[str, int]:
    ws = ensure_worksheet(core, "Products", PRODUCT_COLUMNS)
    package_ws = ensure_worksheet(core, "PackageIdentifiers", PACKAGE_IDENTIFIER_COLUMNS)
    existing = pd.DataFrame(ws.get_all_records())
    for column in PRODUCT_COLUMNS:
        if column not in existing.columns:
            existing[column] = ""
    existing = existing[PRODUCT_COLUMNS]
    initial_count = len(existing)
    added = 0
    updated_count = 0
    package_existing = pd.DataFrame(package_ws.get_all_records())
    package_ids = set(package_existing.get("PackageIdentifierId", pd.Series(dtype=str)).astype(str)) if not package_existing.empty else set()
    packages_added = 0
    candidates = 0

    if data is not None and not data.empty:
        for _, item in data.iterrows():
            raw_row = item.to_dict()
            candidate = transaction_row_to_product(raw_row)
            if not candidate["ProductName"] and not identifier_set(candidate):
                continue
            candidates += 1
            existing, index, created, changed = _upsert_product_in_memory(existing, candidate)
            product_row = existing.loc[index].to_dict()
            if created:
                ws.append_row([product_row.get(column, "") for column in PRODUCT_COLUMNS], value_input_option="RAW")
                added += 1
            elif changed:
                _write_product_row(ws, index + 2, product_row)
                updated_count += 1
            package_row = package_row_from_transaction(raw_row, clean(product_row.get("ProductId")))
            if package_row and package_row["PackageIdentifierId"] not in package_ids:
                package_ws.append_row([package_row.get(column, "") for column in PACKAGE_IDENTIFIER_COLUMNS], value_input_option="RAW")
                package_ids.add(package_row["PackageIdentifierId"])
                packages_added += 1

    return {
        "added": added,
        "updated": updated_count,
        "candidates": candidates,
        "existing": initial_count,
        "packages_added": packages_added,
    }


def schema_overview_df() -> pd.DataFrame:
    return pd.DataFrame(
        [{"Φύλλο": sheet_name, "Στήλες": ", ".join(columns)} for sheet_name, columns in SHEET_SCHEMAS.items()]
    )
