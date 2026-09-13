# Project state · apothiki-mobile

Updated: 2026-09-13

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

2026-09-13 · Added the repository-level self-improving workflow: persistent state, agent operating rules, and reusable pharmacy-inventory skill.

Next: when the next functional change or bug fix is made, follow `AGENTS.md`, add the relevant regression test, and update this file with what was verified.
