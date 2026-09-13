---
name: pharmacy-inventory
description: Safely change and verify the pharmacy inventory app, especially barcode matching, imports, expiry handling, stock movements, FEFO, reversals, and search.
---

# Pharmacy inventory engineering skill

## Purpose

Use this skill for any change touching `app_inventory_stable.py`, catalog/import logic, barcode lookup, expiry handling, stock movements, Google Sheets persistence, or related tests.

## Core invariants

### Product identity and barcodes

- A product can legitimately have multiple barcodes/GTINs.
- Keep one canonical product identity and preserve every verified barcode associated with it.
- Do not create duplicate product records just because two valid barcodes exist.
- Do not merge two different products because names are merely similar.
- Normalize whitespace and obvious formatting noise, but do not destroy meaningful digits or invent missing codes.
- Treat exact barcode matches as stronger evidence than fuzzy name matches.

### Import and deduplication

- Imports should be idempotent whenever a stable file, batch, invoice, or row identifier exists.
- Re-importing the same confirmed source must not duplicate stock movements.
- Preserve source data needed for auditability.
- If deduplication confidence is low, require confirmation instead of silently merging.

### Expiry and lot handling

- Preserve verified expiry and LOT values exactly as received after safe parsing/normalization.
- Never invent an expiry date when none exists.
- Warn when expiry is within the configured threshold; current target behavior includes a 6-month warning horizon.
- Boundary conditions matter: add tests around exactly 6 months, just inside, and just outside the warning window.
- Prefer FEFO for stock removal when expiry-bearing lots are available.

### Stock integrity

- Never permit a removal that creates negative stock.
- Fresh-read and post-write validation are required where concurrent writes can race.
- Use compensating movements for reversals. Do not erase transaction history.
- Keep `VoidOf` / movement-kind relationships intact when touching reversal logic.

### Search and lookup

- Barcode lookup should be deterministic and fast before any online or AI fallback.
- Support the existing lookup surfaces: barcode/GTIN, QR fallback fields, PC code, serial number, brand, and product name.
- AI/fuzzy lookup is a fallback, not the source of truth.

## Verification recipe

For each bug or feature:

1. Write down the acceptance criterion.
2. Reproduce the old failure with a focused test when feasible.
3. Make the smallest production change.
4. Run the focused test.
5. Run related tests.
6. Run `python -m pytest -q` before considering the change complete when practical.
7. If a new reusable failure mode was discovered, add it under **Known failure modes** below.
8. Update `STATE.md` with the verified result and next step.

## Regression checklist

Before merging changes in sensitive areas, check these cases:

- same canonical product with multiple verified barcodes,
- exact duplicate import rows,
- near-duplicate names that must remain separate,
- repeated save/import of the same source,
- missing expiry,
- expiry exactly at warning boundary,
- expiry within warning boundary,
- FEFO removal across multiple lots,
- attempted negative stock,
- reversal preserving history,
- PC-only and SN-only QR fallback,
- exact barcode lookup before fuzzy fallback.

## Known failure modes

Add only confirmed, reusable lessons here.

- **Duplicate catalog identity from alternate barcode**: when two barcodes belong to the same verified product, model this as aliases of one product rather than two products.
- **Repeated import duplication**: derive stable transaction identifiers from stable source/batch/row identity and check them before writing again.
- **Expiry warning off-by-one**: date thresholds need explicit boundary tests rather than visual inspection of the UI.

## Anti-patterns

- Do not “fix” a duplicate by deleting data before proving which row is canonical.
- Do not use fuzzy matching as an automatic merge authority.
- Do not hide failed imports or malformed rows. Surface them for review.
- Do not disable a failing regression test just to make CI green.
- Do not rewrite the entire inventory flow for a local bug when a small verified change is sufficient.

## Memory contract

At session start, read `STATE.md`.
At session end, record:

- what was changed,
- what tests passed,
- what remains uncertain,
- and any new reusable rule that belongs in this skill.
