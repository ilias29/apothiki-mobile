# Project state · apothiki-mobile

Updated: 2026-09-23

## Verified facts

- Main Streamlit application: `app_inventory_stable.py`.
- Tests are run from the repository root with `python -m pytest -q`.
- The application supports barcode/QR lookup, stock by location, FEFO removal, negative-stock protection, and compensating ledger reversals.
- AI-assisted receiving requires explicit user confirmation before stock is written.
- Existing offline/manual imports are designed to use stable transaction identifiers so repeated saves do not duplicate already-written rows.

## General rules

- One real product may have multiple valid barcodes. Preserve all known barcodes, but do not create duplicate product records solely because the barcode differs.
- Never deduplicate different products on weak name similarity alone.
- Expiry handling must preserve the source value exactly when present and must not fabricate dates when absent.
- Expiry alerts should treat products reaching the configured warning window as actionable without rewriting the underlying expiry date.
- Any fix for import, barcode matching, expiry handling, stock movement, or deduplication should add a regression test when feasible.
- Prefer deterministic matching rules first; use fuzzy/AI matching only as a fallback and keep confirmation for uncertain matches.

## Known regression targets

Keep explicit regression coverage for:

- products with two or more barcodes,
- duplicate rows in imported catalog files,
- barcode normalization without loss of leading zeros where meaningful,
- expiry dates near the 6-month warning boundary,
- repeated import/save attempts,
- stock removal that would go below zero,
- FEFO lot selection,
- reversal/compensation movements,
- lookup by barcode, PC code, serial, brand, and product name.

## Open failures / investigations

- No new confirmed failure recorded by this setup change.
- When a new production bug is found, record reproduction steps here before or alongside the fix.

## Last session

2026-09-23 · Added automatic `+1` stock movement for a recognized, locally known barcode from either the live scanner or a barcode photo. The movement targets location 0 (Αποθήκη), records that expiry/lot were not captured, and never fabricates them. A per-scan token prevents Streamlit reruns or the same photo from adding repeatedly; the same barcode can add again after a distinct scan event. Each event also gets a stable transaction id within the scanner session, so a retry cannot duplicate a movement after an uncertain response. Unknown barcodes continue to the existing matching form and are not auto-saved.

Verified with `python -m pytest -q`: 140 tests and 4 subtests passed. Application version: `2026.09.23.1`.

Next: verify one known barcode and one unknown barcode in the deployed mobile Streamlit camera flow after the GitHub deployment finishes.
