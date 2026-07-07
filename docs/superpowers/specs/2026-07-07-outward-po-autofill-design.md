# Outward PO Upload — Auto-fill PO/Invoice Number from CSV

**Date:** 2026-07-07
**Scope:** Frontend only, `Upload Outward PO` page. No backend, schema, or API changes.

## Problem

On the Outward PO upload page, the user must manually type the PO/Invoice Number
before they're even allowed to pick a CSV file — even though the CSV already
contains a `po_number` column. This is redundant effort and a source of
typos/mismatches between the typed value and the file's actual content.

## Goal

Derive the PO/Invoice Number from the uploaded CSV automatically wherever
possible, while preserving the existing upload workflow, preview popup,
backend validation, and business logic exactly as they are today.

## Design

### 1. Relax the file-select guard

`UploadOutwardPO.tsx`'s `handleFileChange` currently blocks file selection
unless both `customerId` and `poNumber` are set. Change the guard to require
only `customerId` (error message: `"Select a customer before choosing a
file."`). The PO number is no longer a prerequisite for picking a file since
it will usually come from the file itself. Nothing else about file selection
(opening the preview modal, calling `previewPO.mutate(fd)`) changes — that
call has never depended on `po_number`.

### 2. New CSV parsing helper — `lib/csvPreview.ts`

Add a new exported function `extractPoNumberForAutofill(csvText: string)`,
reusing the existing private `splitCsvLine` helper already in that file. It
is additive only — the existing `parseInwardPOPreview` (used exclusively by
the Inward `UploadPO.tsx` page) is untouched, so Inward is unaffected.

Behavior:
- No `po_number` column in the header → `{ status: 'none' }`
- Scan every data row, collecting distinct **non-blank** `po_number` values
  (rows with a blank/missing `po_number` are skipped, not counted as a
  distinct value)
- Exactly one distinct value → `{ status: 'single', poNumber }`
- More than one distinct value → `{ status: 'multiple' }`
- Zero non-blank values found → `{ status: 'none' }`

### 3. Wiring in `UploadOutwardPO.tsx`

In `handleFileChange`, after the existing `previewPO.mutate(fd)` call fires
unchanged, restore the field to its normal editable state
(`setPoNumberLocked(false)`, `setPoNumberWarning('')`) and then read the
file as text (`selected.text()`) and run the new helper:

- `single` → `setPoNumber(poNumber)` (overwrites any prior value) and
  `setPoNumberLocked(true)` — the field becomes `readOnly` since the CSV is
  now the source of truth.
- `multiple` → `setPoNumber('')` (clear any previously auto-filled value),
  field stays editable, and set `poNumberWarning` to the exact string:
  `"Multiple PO numbers found in this CSV. Please upload a single PO per
  file."`
- `none` → do nothing further; field stays editable, existing manual-entry
  and backend validation behavior is preserved.

A request-id ref guards against a slow-resolving parse from a previously
selected file overwriting state if the user quickly picks a second file
before the first `.text()` promise resolves.

### 4. Read-only-when-auto-filled

`poNumberLocked` is passed to the existing `Input`'s `readOnly` prop (not
`disabled`). The value stays fully visible, selectable, and copyable with
normal input styling (not grayed out) — the user just can't type into it
while it reflects the CSV. It reverts to editable (`poNumberLocked = false`)
as soon as:
- the user cancels/closes the preview (`closePreview`), or
- the user selects a different CSV (start of `handleFileChange`, before the
  new file's parse result is applied).

### 5. Displaying the multiple-PO warning

`poNumberWarning` is passed to the existing `Input`'s `error` prop, which
already renders a red border and red helper text below the field — no new
UI component needed.

### 6. Clearing on cancel

In `closePreview` (existing "cancel/close preview" handler), also reset
`poNumber`, `poNumberWarning`, and `poNumberLocked` to their empty/default
state. This is the only place the PO number is cleared due to file removal.

The existing "upload failed, keep the file cleared but preserve `poNumber`
for a quick retry" behavior in `handleImport`'s catch block is preserved as
today — we only additionally reset `poNumberLocked` to `false` there (since
the file is gone, the field must not stay non-editable), without touching
the preserved `poNumber` value. The success path already resets `poNumber`
to `''`; it now also resets `poNumberLocked` to `false` for consistency.

## Out of scope / unchanged

- No backend endpoint, schema, or business-logic changes.
- No change to the preview popup's own validation/problems display (backend
  already independently detects "multiple PO numbers in file" for the
  popup's own `problems` list — that is separate from this field-level
  auto-fill warning and is not touched).
- Inward `UploadPO.tsx` and its use of `parseInwardPOPreview` are untouched.
- RBAC, other pages, and all other validation rules are untouched.

## Testing (manual)

1. Select customer → pick a CSV where every row has the same `po_number` →
   field auto-fills and becomes disabled (grayed out).
2. Pick a different CSV with a different single `po_number` → field updates
   to the new value, stays disabled.
3. Pick a CSV with two different `po_number` values → field is cleared (any
   previous auto-filled value is removed) and stays editable; red helper
   text reads "Multiple PO numbers found in this CSV. Please upload a single
   PO per file."
4. Pick a CSV with no `po_number` column → field stays empty and editable;
   existing missing-column error still surfaces via the preview popup as
   before.
5. Cancel the preview popup → field clears and becomes editable again.
6. Manually confirm the Inward "Upload PO" page is completely unaffected.
