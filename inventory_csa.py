import hashlib
import re
from datetime import datetime
from typing import Any

import pandas as pd

import app_inventory_search as core


AI_COLUMNS = [
    "RowId", "confirm", "ProductName", "Brand", "BarcodeOrGTIN", "GTIN",
    "Category", "Quantity", "NetPrice", "VATRate", "GrossPrice",
    "ExpiryDate", "LotNumber", "SerialNumber", "Strength", "DosageForm",
    "Confidence", "Notes",
]


def clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def upper(value: Any) -> str:
    return " ".join(clean(value).upper().split())


def number(value: Any, default: float = 0.0) -> float:
    text = clean(value).replace("€", "").replace("%", "").replace(" ", "")
    if not text:
        return default
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def identifier(value: Any) -> str:
    text = clean(value)
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def normalize_ai_items(items: list[dict[str, Any]], default_vat: float = 24.0) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(items or [], start=1):
        raw = dict(item or {})
        vat = number(raw.get("VATRate"), default_vat)
        if 0 < vat <= 1:
            vat *= 100
        net = number(raw.get("NetPrice"), 0.0)
        gross = number(raw.get("GrossPrice"), 0.0)
        if net <= 0 < gross:
            net = gross / (1 + vat / 100)
        if gross <= 0 < net:
            gross = net * (1 + vat / 100)
        try:
            qty = max(0, int(round(float(raw.get("Quantity") or 0))))
        except Exception:
            qty = 0
        gtin = identifier(raw.get("GTIN"))
        barcode = identifier(raw.get("BarcodeOrGTIN")) or gtin
        serial = clean(raw.get("SerialNumber"))
        if serial and qty == 0:
            qty = 1
        rows.append({
            "RowId": idx,
            "confirm": False,
            "ProductName": upper(raw.get("ProductName")),
            "Brand": upper(raw.get("Brand")),
            "BarcodeOrGTIN": barcode,
            "GTIN": gtin,
            "Category": clean(raw.get("Category")) or "Φάρμακο",
            "Quantity": qty,
            "NetPrice": round(net, 2),
            "VATRate": round(vat, 2),
            "GrossPrice": round(gross, 2),
            "ExpiryDate": clean(raw.get("ExpiryDate")),
            "LotNumber": clean(raw.get("LotNumber")),
            "SerialNumber": serial,
            "Strength": upper(raw.get("Strength")),
            "DosageForm": upper(raw.get("DosageForm")),
            "Confidence": clean(raw.get("Confidence")) or "low",
            "Notes": clean(raw.get("Notes")),
        })
    return pd.DataFrame(rows, columns=AI_COLUMNS)


def internal_identity(product: Any, brand: Any = "", strength: Any = "") -> str:
    raw = "|".join([upper(product), upper(brand), upper(strength)]).encode("utf-8")
    return "INT-" + hashlib.sha256(raw).hexdigest()[:16].upper()


def row_identity(row: pd.Series | dict[str, Any]) -> tuple[str, str, str, str]:
    get = row.get
    explicit_gtin = identifier(get("GTIN", ""))
    code = identifier(get("BarcodeOrGTIN", ""))
    if explicit_gtin:
        return "GTIN", explicit_gtin, "", explicit_gtin
    if code.isdigit() and len(code) == 14:
        return "GTIN", code, "", code
    if code:
        return "Barcode", code, code, ""
    internal = internal_identity(get("ProductName", ""), get("Brand", ""), get("Strength", ""))
    return "Internal", internal, "", ""


def receipt_transaction(
    row: pd.Series,
    *,
    location_id: int,
    transaction_id: str,
    source_note: str,
) -> dict[str, Any]:
    qty = int(row.get("Quantity", 0))
    serial = clean(row.get("SerialNumber", ""))
    if qty <= 0:
        raise ValueError("Η ποσότητα παραλαβής πρέπει να είναι μεγαλύτερη από 0.")
    if serial and qty != 1:
        raise ValueError(f"Το {clean(row.get('ProductName'))} έχει SN, άρα Quantity=1.")
    code_type, code_value, barcode, gtin = row_identity(row)
    note_parts = [source_note]
    if number(row.get("NetPrice"), 0) > 0:
        note_parts.append(f"net={number(row.get('NetPrice')):.2f}")
    if number(row.get("VATRate"), 0) > 0:
        note_parts.append(f"vat={number(row.get('VATRate')):.2f}%")
    if number(row.get("GrossPrice"), 0) > 0:
        note_parts.append(f"gross={number(row.get('GrossPrice')):.2f}")
    if clean(row.get("Confidence")):
        note_parts.append(f"ai_confidence={clean(row.get('Confidence'))}")
    if clean(row.get("Notes")):
        note_parts.append(clean(row.get("Notes")))
    return core.make_transaction(
        code_type=code_type,
        code_value=code_value,
        barcode=barcode,
        gtin=gtin,
        serial_number=serial,
        lot_number=clean(row.get("LotNumber")),
        expiry_date=clean(row.get("ExpiryDate")),
        strength=upper(row.get("Strength")),
        dosage_form=upper(row.get("DosageForm")),
        brand=upper(row.get("Brand")),
        product=upper(row.get("ProductName")),
        category=clean(row.get("Category")) or "Φάρμακο",
        location_id=location_id,
        movement="CSA Παραλαβή (+)",
        quantity=qty,
        delta=qty,
        note="; ".join(part for part in note_parts if clean(part)),
        movement_kind=core.NORMAL,
        transaction_id=transaction_id,
    )


def stock_snapshot(data: pd.DataFrame) -> pd.DataFrame:
    if data is None or data.empty:
        return pd.DataFrame()
    frame = core.active_movements(data).copy()
    if frame.empty:
        return frame
    frame["DeltaQty"] = pd.to_numeric(frame["DeltaQty"], errors="coerce").fillna(0).astype(int)
    group_cols = [
        "CodeType", "CodeValue", "Barcode", "GTIN", "SerialNumber", "LotNumber",
        "ExpiryDate", "Strength", "DosageForm", "Μάρκα", "Προϊόν", "Κατηγορία",
        "LocationId", "Τοποθεσία",
    ]
    for col in group_cols:
        if col not in frame.columns:
            frame[col] = ""
        frame[col] = frame[col].fillna("")
    grouped = frame.groupby(group_cols, dropna=False, as_index=False)["DeltaQty"].sum()
    grouped = grouped.rename(columns={"DeltaQty": "Stock"})
    grouped = grouped[grouped["Stock"] > 0].copy()
    if grouped.empty:
        return grouped
    expiry = pd.to_datetime(grouped["ExpiryDate"], errors="coerce")
    grouped["_expiry_sort"] = expiry.fillna(pd.Timestamp.max)
    return grouped.sort_values(["Προϊόν", "LocationId", "_expiry_sort", "LotNumber"]).drop(columns=["_expiry_sort"])


def product_summary(snapshot: pd.DataFrame) -> pd.DataFrame:
    if snapshot is None or snapshot.empty:
        return pd.DataFrame()
    grouped = snapshot.groupby(
        ["CodeType", "CodeValue", "Barcode", "GTIN", "Strength", "DosageForm", "Μάρκα", "Προϊόν", "Κατηγορία", "LocationId", "Τοποθεσία"],
        dropna=False,
        as_index=False,
    )["Stock"].sum()
    return grouped.sort_values(["Προϊόν", "LocationId"])


def filter_summary(summary: pd.DataFrame, query: str) -> pd.DataFrame:
    if summary is None or summary.empty or not clean(query):
        return summary
    q = clean(query).lower()
    mask = pd.Series(False, index=summary.index)
    for col in ["Προϊόν", "Μάρκα", "Barcode", "GTIN", "CodeValue", "Strength", "Κατηγορία"]:
        if col in summary.columns:
            mask |= summary[col].astype(str).str.lower().str.contains(q, regex=False, na=False)
    return summary[mask].copy()


def matching_lots(snapshot: pd.DataFrame, product_row: pd.Series) -> pd.DataFrame:
    if snapshot is None or snapshot.empty:
        return pd.DataFrame()
    mask = (
        snapshot["CodeType"].astype(str).eq(clean(product_row.get("CodeType")))
        & snapshot["CodeValue"].astype(str).eq(clean(product_row.get("CodeValue")))
        & snapshot["LocationId"].astype(int).eq(int(product_row.get("LocationId")))
    )
    return snapshot[mask].copy()


def match_ai_item_to_summary(summary: pd.DataFrame, item: pd.Series) -> pd.DataFrame:
    if summary is None or summary.empty:
        return pd.DataFrame()
    gtin = identifier(item.get("GTIN", ""))
    code = identifier(item.get("BarcodeOrGTIN", ""))
    if gtin:
        matches = summary[summary["GTIN"].astype(str).eq(gtin)]
        if not matches.empty:
            return matches
    if code:
        matches = summary[
            summary["Barcode"].astype(str).eq(code)
            | summary["GTIN"].astype(str).eq(code)
            | summary["CodeValue"].astype(str).eq(code)
        ]
        if not matches.empty:
            return matches
    product = upper(item.get("ProductName", ""))
    brand = upper(item.get("Brand", ""))
    strength = upper(item.get("Strength", ""))
    matches = summary[summary["Προϊόν"].astype(str).map(upper).eq(product)] if product else summary.iloc[0:0]
    if brand and not matches.empty:
        branded = matches[matches["Μάρκα"].astype(str).map(upper).eq(brand)]
        if not branded.empty:
            matches = branded
    if strength and not matches.empty:
        strengthened = matches[matches["Strength"].astype(str).map(upper).eq(strength)]
        if not strengthened.empty:
            matches = strengthened
    return matches


def fefo_issue_transactions(
    snapshot: pd.DataFrame,
    product_row: pd.Series,
    *,
    quantity: int,
    note: str,
    transaction_prefix: str,
) -> list[dict[str, Any]]:
    qty = int(quantity)
    if qty <= 0:
        raise ValueError("Η ποσότητα εξόδου πρέπει να είναι μεγαλύτερη από 0.")
    lots = matching_lots(snapshot, product_row)
    available = int(pd.to_numeric(lots.get("Stock", pd.Series(dtype=int)), errors="coerce").fillna(0).sum())
    if available < qty:
        raise core.InventoryError(f"Δεν υπάρχει αρκετό stock. Διαθέσιμα: {available}, ζητήθηκαν: {qty}.")
    lots = lots.copy()
    lots["_expiry"] = pd.to_datetime(lots["ExpiryDate"], errors="coerce").fillna(pd.Timestamp.max)
    lots = lots.sort_values(["_expiry", "LotNumber", "SerialNumber"]).drop(columns=["_expiry"])
    remaining = qty
    txs: list[dict[str, Any]] = []
    part = 1
    for _, lot in lots.iterrows():
        if remaining <= 0:
            break
        lot_stock = int(lot["Stock"])
        take = min(remaining, lot_stock)
        txs.append(core.make_transaction(
            code_type=clean(lot["CodeType"]),
            code_value=clean(lot["CodeValue"]),
            barcode=clean(lot["Barcode"]),
            gtin=clean(lot["GTIN"]),
            serial_number=clean(lot["SerialNumber"]) if take == 1 else "",
            lot_number=clean(lot["LotNumber"]),
            expiry_date=clean(lot["ExpiryDate"]),
            strength=clean(lot["Strength"]),
            dosage_form=clean(lot["DosageForm"]),
            brand=clean(lot["Μάρκα"]),
            product=clean(lot["Προϊόν"]),
            category=clean(lot["Κατηγορία"]),
            location_id=int(lot["LocationId"]),
            movement="CSA Έξοδος (-)",
            quantity=take,
            delta=-take,
            note=note,
            movement_kind=core.NORMAL,
            transaction_id=f"{transaction_prefix}-{part:02d}",
        ))
        remaining -= take
        part += 1
    return txs


def batch_seed(image_bytes: list[bytes], reference: str = "") -> str:
    digest = hashlib.sha256()
    ref = clean(reference)
    if ref:
        digest.update(("document|" + ref).encode("utf-8"))
    else:
        for value in image_bytes:
            digest.update(value)
    return digest.hexdigest()[:18]


def source_note(*, supplier: str = "", reference: str = "", document_date: str = "") -> str:
    pieces = ["source=CSA_AI"]
    if clean(supplier):
        pieces.append(f"supplier={clean(supplier)}")
    if clean(reference):
        pieces.append(f"document={clean(reference)}")
    if clean(document_date):
        pieces.append(f"document_date={clean(document_date)}")
    pieces.append(f"captured_at={datetime.now().isoformat(timespec='seconds')}")
    return "; ".join(pieces)
