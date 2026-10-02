# Project state · apothiki-mobile

Updated: 2026-10-02

## Verified facts

- Main Streamlit application: `app_inventory_stable.py`.
- Tests are run from the repository root with `python -m pytest -q`.
- The application supports barcode/QR lookup, stock by location, FEFO removal, negative-stock protection, and compensating ledger reversals.
- AI-assisted receiving requires explicit user confirmation before stock is written.
- Existing offline/manual imports are designed to use stable transaction identifiers so repeated saves do not duplicate already-written rows.
- The Stock tab supports editing saved lots, including a visible editable expiry field and a confirmed action to zero only the selected lot. Corrections append movements, keep the barcode fixed, and preserve previous ledger rows.
- Product master display details can be deliberately corrected while preserving its canonical ProductId and known barcode/GTIN aliases.
- The Stock-tab editor uses distinct forms for zeroing and editing. Their confirmation checkboxes are validated on submission because form widgets do not update the server until a submit action.

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

- The receiving form showed a selected expiry date on mobile but submitted `None`; the save error is fixed by validated text entry and regression tests. Verify the deployed Streamlit app after GitHub Actions passes.

## Last session

2026-10-02 · Fixed the reported Stock-tab controls. The selected-lot editor always shows an editable expiry text field (DD/MM/YYYY or MM/YYYY; blank clears expiry) and has a separately confirmed button that zeros only the selected lot through a ledger movement. The receiving form now uses validated expiry text entry because the mobile date widget showed a value but submitted `None`; added regression coverage for both accepted date formats, invalid input, and explicit no-expiry. CI status for this follow-up is pending. Application version: `2026.10.02.3`.

Previous session: added a Stock-tab editor for saved lots. It fresh-checks the selected lot before changes, writes deterministic correction movements (the removal/replacement pair is batched when the replacement quantity is positive), keeps the previous movement history, and updates confirmed product details without changing ProductId or removing barcode aliases. Prior local `py_compile` and `git diff --check` passed; the full pytest suite could not run locally because pytest is missing. Application version then: `2026.10.02.1`.

Previous: 2026-09-23 · Added automatic `+1` stock movement for a recognized, locally known barcode from either the live scanner or a barcode photo. The movement targets location 0 (Αποθήκη), records that expiry/lot were not captured, and never fabricates them. A per-scan token prevents Streamlit reruns or the same photo from adding repeatedly; the same barcode can add again after a distinct scan event. Each event also gets a stable transaction id within the scanner session, so a retry cannot duplicate a movement after an uncertain response. Unknown barcodes continue to the existing matching form and are not auto-saved.

Verified then with `python -m pytest -q`: 140 tests and 4 subtests passed. Application version at that time: `2026.09.23.1`.

Next: in the deployed app, edit one test lot's expiry and zero another test lot.
