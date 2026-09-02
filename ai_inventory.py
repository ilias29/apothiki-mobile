import base64
import json
import mimetypes
from typing import Any

from openai import OpenAI


ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ProductName": {"type": "string"},
                    "Brand": {"type": "string"},
                    "BarcodeOrGTIN": {"type": "string"},
                    "Category": {"type": "string"},
                    "NetPrice": {"type": ["number", "null"]},
                    "VATRate": {"type": ["number", "null"]},
                    "GrossPrice": {"type": ["number", "null"]},
                    "Quantity": {"type": ["integer", "null"]},
                    "ExpiryDate": {"type": "string"},
                    "LotNumber": {"type": "string"},
                    "SerialNumber": {"type": "string"},
                    "GTIN": {"type": "string"},
                    "QRRawData": {"type": "string"},
                    "DataMatrixRawData": {"type": "string"},
                    "Strength": {"type": "string"},
                    "DosageForm": {"type": "string"},
                    "Confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "Notes": {"type": "string"},
                },
                "required": [
                    "ProductName", "Brand", "BarcodeOrGTIN", "Category",
                    "NetPrice", "VATRate", "GrossPrice", "Quantity",
                    "ExpiryDate", "LotNumber", "SerialNumber", "GTIN",
                    "QRRawData", "DataMatrixRawData", "Strength", "DosageForm",
                    "Confidence", "Notes"
                ],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["items", "warnings"],
    "additionalProperties": False,
}


def _data_url(file_bytes: bytes, filename: str = "image.jpg", mime_type: str = "") -> str:
    mime = mime_type or mimetypes.guess_type(filename or "")[0] or "image/jpeg"
    encoded = base64.b64encode(file_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _instructions(mode: str, default_vat: float) -> str:
    common = f"""
You extract pharmacy inventory data from images. Return only data supported by visible evidence.
Never invent a barcode, GTIN, lot, expiry, serial number, product name, price, or quantity.
If a field is unreadable or absent, return an empty string or null.
Use ISO date YYYY-MM-DD when a full expiry date is visible. If only MM/YYYY is visible, use the last day of that month.
Keep long identifiers as strings, preserving leading zeroes.
Default VAT is {default_vat}% only when a price is present and the VAT is not visible; mention this assumption in Notes.
Quantity must be the count you can justify from the image. If uncertain, set Confidence to low and explain in Notes.
Do not treat a serial number as a product-level identifier. One serial number represents one physical pack.
Do not infer values from typical pharmacy pricing, package sizes, or prior knowledge when they are not visible.
"""
    if mode == "Φάρμακο / DataMatrix":
        return common + """
Focus on medicine pack traceability. Extract GTIN, LOT, EXP and SN when visible, and raw DataMatrix/QR content only if actually readable.
If GTIN is extracted from a machine-readable code, also place it in BarcodeOrGTIN.
For a row with a SerialNumber, Quantity must be 1.
"""
    if mode == "Τιμολόγιο / παραλαβή":
        return common + """
The image is a pharmacy supplier invoice, delivery note, order-receipt screen, or computer screen showing invoice rows.
Create one item per actual product line. Quantity means the delivered/received quantity on that line, not pack size, unit price, discount, line number, or tax rate.
Prefer the clearly labelled delivered/invoiced quantity when several numeric columns exist. If the quantity column is ambiguous, set Quantity to null, Confidence to low, and explain why in Notes.
Extract barcode/GTIN only when it is visibly tied to that row. Preserve product strength and dosage form when visible.
Ignore totals, subtotals, VAT summary rows, payment lines, headers, footers, and non-product service rows.
If a line is repeated on another photo because pages overlap, include it only once when you can confidently identify the duplicate; otherwise warn about the possible duplicate.
"""
    if mode == "Έξοδος / πωλήσεις":
        return common + """
The image is a list or screen of pharmacy items that were sold, dispensed, transferred out, or otherwise left stock.
Create one item per visible product row. Quantity means the quantity that left stock. Do not infer hidden sales or cumulative totals.
Prioritize barcode/GTIN when visible because it is used for exact stock matching. If product identity is ambiguous, keep Confidence low and explain in Notes.
Ignore money totals, discounts, payment fields, customer information, and rows that are not products.
"""
    if mode == "Συγκεντρωτικό / τιμοκατάλογος":
        return common + """
The image is a supplier summary, price list, invoice-like table, or handwritten/printed inventory list.
Create one item per visible product row. Prioritize ProductName, Brand, BarcodeOrGTIN, Quantity and prices.
If only a gross price is visible, put it in GrossPrice. If only a net price is clearly labeled, put it in NetPrice.
"""
    return common + """
The image shows shelves, drawers, or stock. Create one item per distinguishable product and estimate only clearly visible quantities.
Do not infer hidden boxes. Prefer a lower quantity with a low-confidence note over an invented total.
"""


def analyze_images(
    images: list[dict[str, Any]],
    *,
    api_key: str,
    mode: str,
    default_vat: float = 24.0,
    model: str = "gpt-5.6-terra",
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Λείπει το OPENAI_API_KEY από τα Streamlit Secrets.")
    if not images:
        raise ValueError("Δεν ανέβηκε εικόνα.")

    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": _instructions(mode, default_vat)}
    ]
    for image in images:
        data = image.get("bytes") or b""
        if not data:
            continue
        content.append({
            "type": "input_image",
            "image_url": _data_url(data, image.get("name", "image.jpg"), image.get("type", "")),
            "detail": "high",
        })

    if len(content) == 1:
        raise ValueError("Οι εικόνες είναι κενές ή δεν διαβάστηκαν.")

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
        text={
            "format": {
                "type": "json_schema",
                "name": "pharmacy_inventory_extraction",
                "strict": True,
                "schema": ITEM_SCHEMA,
            }
        },
        store=False,
    )
    text = response.output_text
    if not text:
        raise ValueError("Το AI δεν επέστρεψε αποτέλεσμα.")
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Το AI επέστρεψε μη έγκυρο JSON.") from exc
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise ValueError("Το αποτέλεσμα του AI δεν έχει την αναμενόμενη μορφή.")
    return result
