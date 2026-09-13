# Agent operating rules

This repository is maintained with a learn-from-failures workflow. Any coding agent working here must follow these rules.

## Session start

1. Read `STATE.md`.
2. Read `skills/pharmacy-inventory/SKILL.md`.
3. Read the relevant tests before modifying production code.
4. Treat `app_inventory_stable.py` as the main Streamlit application unless `README.md` says otherwise.

## Work loop

For every non-trivial change use this loop:

1. **Fail / Observe** — identify the concrete bug, missing behavior, or acceptance criterion.
2. **Investigate** — find the actual cause. Do not patch symptoms blindly.
3. **Verify** — add or update an automated test that proves the behavior.
4. **Fix** — make the smallest safe production change.
5. **Re-verify** — run the relevant tests, then the full suite when practical.
6. **Distill** — if the failure teaches a reusable lesson, add it to `skills/pharmacy-inventory/SKILL.md`.
7. **Record** — update `STATE.md` with verified facts, open failures, and next steps.

## Independent verification

Do not rely only on self-review. For meaningful changes, verify using at least one independent signal:

- an automated regression test,
- a separate review pass focused only on acceptance criteria,
- or, for visual UI changes, a screenshot comparison against the stated goal.

A change is not complete just because the code looks plausible. Humans already invented enough plausible bugs.

## Safety rules for pharmacy inventory data

- Never silently drop a barcode, lot, expiry, serial, or stock movement.
- Never merge two products only because their names are vaguely similar.
- When the same verified product has multiple barcodes, preserve all barcodes while keeping one canonical product record.
- Imports must be idempotent where a stable source identifier is available.
- Never allow a stock-removal path to create negative stock.
- Preserve the compensating-ledger model. Reverse with a new movement rather than deleting history.
- Prefer FEFO when removing from lots that have expiry dates.
- A missing value is not a verified value. Do not invent expiry dates, barcodes, quantities, PC codes, serial numbers, or lot numbers.

## Definition of done

A non-trivial change is done only when:

- acceptance criteria are satisfied,
- regression coverage exists when feasible,
- no known related test is failing,
- reusable lessons are written into the skill,
- and `STATE.md` has an accurate resume pointer.
