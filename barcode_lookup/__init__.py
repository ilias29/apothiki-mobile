"""Barcode lookup with the pharmacy master sheet as the first fallback.

The existing barcode_lookup.py remains the web/provider implementation.  This
package intentionally shadows that module and delegates to it only when the
pharmacy master sheet has no exact barcode match.
"""
from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
import re
from typing import Any


_MASTER_SPREADSHEET_ID = "1pb-nKSzOThKAi2BMXy9z63-IwigfXccml3CViDK1GOo"
_MASTER_TABS = ("Sheet1", "Sheet2")
_MASTER_BARCODE_COLUMN = 8
_MASTER_NAME_COLUMN = 1


# Load the previous implementation under a private module name, then re-export
# its public/testing helpers so existing callers and tests keep working.
_legacy_path = Path(__file__).resolve().parent.parent / "barcode_lookup.py"
_spec = importlib.util.spec_from_file_location("_barcode_lookup_legacy", _legacy_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load legacy barcode lookup from {_legacy_path}")
_legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_legacy)
for _name, _value in vars(_legacy).items():
    if not _name.startswith("__") and _name != "lookup_barcode_online":
        globals()[_name] = _value


def _master_candidate(barcode: str, product_name: str) -> dict[str, Any]:
    is_gtin14 = barcode.isdigit() and len(barcode) == 14
    attributes = {"strength": "", "dosage_form": "", "package_size": ""}
    try:
        import app_inventory_search as inventory_search
        attributes = inventory_search.extract_commercial_attributes(product_name)
    except Exception:
        pass
    return {
        "product_name": product_name.strip(),
        "brand": "",
        "barcode": "" if is_gtin14 else barcode,
        "gtin": barcode if is_gtin14 else "",
        "strength": attributes.get("strength", ""),
        "dosage_form": attributes.get("dosage_form", ""),
        "package_size": attributes.get("package_size", ""),
        "category": "Άλλο",
        "source": "Βάση barcode φαρμακείου",
        "url": "",
        "confidence": 1.0,
        "verified": True,
    }


@lru_cache(maxsize=4096)
def lookup_pharmacy_master(barcode: str) -> tuple[dict[str, Any], ...]:
    """Return exact matches from the imported pharmacy barcode workbook.

    Duplicate barcodes are deliberately returned as separate candidates so the
    operator can choose the correct description instead of silently accepting a
    wrong product name.
    """
    barcode = re.sub(r"\s+", "", str(barcode or "").strip())
    if not barcode or not barcode.isdigit():
        return ()
    try:
        import app_inventory_search as inventory_search
        client = inventory_search.worksheet().spreadsheet.client
        book = client.open_by_key(_MASTER_SPREADSHEET_ID)
    except Exception:
        return ()

    found: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for tab_name in _MASTER_TABS:
        try:
            ws = book.worksheet(tab_name)
            cells = ws.findall(barcode, in_column=_MASTER_BARCODE_COLUMN)
        except Exception:
            continue
        for cell in cells:
            try:
                product_name = str(ws.cell(cell.row, _MASTER_NAME_COLUMN).value or "").strip()
            except Exception:
                continue
            key = " ".join(product_name.casefold().split())
            if product_name and key not in seen_names:
                seen_names.add(key)
                found.append(_master_candidate(barcode, product_name))
        # The two tabs are consecutive report sections. Once an exact match is
        # found there is no reason to spend another API call on the next tab.
        if found:
            break
    return tuple(found)


def _sync_legacy_test_hooks() -> None:
    """Keep monkeypatch-based tests compatible with the shadow package."""
    for name in ("_verified_provider_candidates", "_fallback_search_candidates", "_search_ddg"):
        if name in globals():
            setattr(_legacy, name, globals()[name])


def lookup_barcode_online(barcode: str) -> list[dict[str, Any]]:
    barcode = re.sub(r"\s+", "", str(barcode or "").strip())
    if not barcode:
        return []

    master = list(lookup_pharmacy_master(barcode))
    if master:
        return master

    _sync_legacy_test_hooks()
    return _legacy.lookup_barcode_online(barcode)
