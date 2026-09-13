"""Build the compressed Streamlit catalogue from a CSA para-pharmacy export."""

from __future__ import annotations

import argparse
import re
from collections import OrderedDict
from pathlib import Path

import pandas as pd


VERIFIED_BARCODE_NAMES = {
    "033984003972": "SOLGAR METHYLCOBALAMIN (B12) 1000 MCG",
}


def text(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).upper()


def barcode(value: object) -> str:
    if pd.isna(value):
        return ""
    digits = "".join(re.findall(r"\d", str(value)))
    return digits if 4 <= len(digits) <= 48 else ""


def read_items(source: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for sheet_name in pd.ExcelFile(source).sheet_names:
        sheet = pd.read_excel(source, sheet_name=sheet_name, header=None, dtype=str)
        current: dict[str, object] | None = None
        for _, row in sheet.iloc[13:].iterrows():
            item_code = text(row.iloc[0])
            product_name = text(row.iloc[2])
            if item_code and product_name:
                current = {"item_code": item_code, "product_name": product_name, "barcodes": []}
                items.append(current)
            detected = barcode(row.iloc[7])
            if current is not None and detected:
                barcodes = current["barcodes"]
                if detected not in barcodes:
                    barcodes.append(detected)
    return items


def build_catalog(items: list[dict[str, object]]) -> tuple[pd.DataFrame, dict[str, int]]:
    products: OrderedDict[str, dict[str, object]] = OrderedDict()
    for item in items:
        product_name = str(item["product_name"])
        key = product_name.casefold()
        product = products.setdefault(key, {"ProductName": product_name, "barcodes": []})
        for code in item["barcodes"]:
            if code not in product["barcodes"]:
                product["barcodes"].append(code)

    barcode_owner: dict[str, str] = {}
    conflicts = 0
    output: list[dict[str, str]] = []
    for key, product in products.items():
        unique_codes: list[str] = []
        for code in product["barcodes"]:
            if code in barcode_owner and barcode_owner[code] != key:
                conflicts += 1
                continue
            barcode_owner[code] = key
            unique_codes.append(code)
        if unique_codes:
            verified_name = next(
                (VERIFIED_BARCODE_NAMES[code] for code in unique_codes if code in VERIFIED_BARCODE_NAMES),
                str(product["ProductName"]),
            )
            output.append({"ProductName": verified_name, "Barcodes": "|".join(unique_codes)})

    frame = pd.DataFrame(output, columns=["ProductName", "Barcodes"])
    stats = {
        "source_items": len(items),
        "catalog_products": len(frame),
        "barcode_mappings": len(barcode_owner),
        "products_with_multiple_barcodes": int(frame["Barcodes"].str.contains("\\|").sum()),
        "barcode_conflicts_removed": conflicts,
        "products_without_usable_barcode": sum(not item["barcodes"] for item in products.values()),
    }
    return frame, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    catalog, stats = build_catalog(read_items(args.source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    catalog.to_csv(args.output, index=False, compression="gzip")
    print(stats)


if __name__ == "__main__":
    main()
