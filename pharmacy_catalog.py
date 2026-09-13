"""Fast local lookup for the pharmacy product/barcode catalogue."""

import base64
import gzip
import io
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd


CATALOG_PATH = Path(__file__).with_name("pharmacy_catalog.csv.gz")


def clean_code(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


@lru_cache(maxsize=1)
def catalog_dataframe() -> pd.DataFrame:
    if not CATALOG_PATH.exists():
        return pd.DataFrame(columns=["ProductName", "Barcodes"])
    compressed = CATALOG_PATH.read_bytes()
    # GitHub's text-file upload route stores the gzip as ASCII base64. Keep
    # compatibility with older deployments that contain the binary gzip.
    if not compressed.startswith(b"\x1f\x8b"):
        compressed = base64.b64decode(compressed)
    return pd.read_csv(io.BytesIO(gzip.decompress(compressed)), dtype=str, keep_default_na=False)


@lru_cache(maxsize=1)
def barcode_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in catalog_dataframe().to_dict("records"):
        product_name = str(row.get("ProductName", "")).strip()
        barcodes = [clean_code(code) for code in str(row.get("Barcodes", "")).split("|")]
        barcodes = [code for code in barcodes if code]
        for code in barcodes:
            index[code] = {
                "product_name": product_name,
                "barcodes": " | ".join(barcodes),
            }
    return index


def lookup_pharmacy_product(code: Any) -> dict[str, str] | None:
    return barcode_index().get(clean_code(code))
