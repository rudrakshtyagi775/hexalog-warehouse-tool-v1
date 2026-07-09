import type { POPreviewData } from '@/components/upload/POPreviewModal'

// Mirrors app/services/inward_service.py's _PO_REQUIRED_COLUMNS exactly.
const REQUIRED_COLUMNS = ['po_number', 'ean', 'ordered_qty']

function splitCsvLine(line: string): string[] {
  const fields: string[] = []
  let current = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const char = line[i]
    if (inQuotes) {
      if (char === '"') {
        if (line[i + 1] === '"') {
          current += '"'
          i++
        } else {
          inQuotes = false
        }
      } else {
        current += char
      }
    } else if (char === '"') {
      inQuotes = true
    } else if (char === ',') {
      fields.push(current)
      current = ''
    } else {
      current += char
    }
  }
  fields.push(current)
  return fields
}

function empty(problem: string): POPreviewData {
  return { rows: [], totalRows: 0, totalQuantity: 0, problems: [problem], isValid: false }
}

/**
 * Client-side preview of an Inward PO CSV. Mirrors the exact validation rules
 * app/services/inward_service.upload_po already enforces server-side: same
 * required columns, same "every row must match the requested PO number" rule,
 * same "ordered_qty must be a positive integer" rule. This is purely a preview
 * — it never replaces the real upload call, which still validates
 * authoritatively (including the PO-already-exists check, which has no
 * client-side equivalent and surfaces on Import exactly as it does today).
 */
const BOM_CHAR_CODE = 0xfeff

export type PoNumberAutofillResult =
  | { status: 'single'; poNumber: string }
  | { status: 'multiple' }
  | { status: 'none' }

/**
 * Scans an Outward PO CSV for a single, consistent po_number to auto-fill
 * the PO/Invoice Number field with. Purely a UX helper — the authoritative
 * "all rows must match the requested PO" check still happens server-side.
 */
export function extractPoNumberForAutofill(csvText: string): PoNumberAutofillResult {
  const text = csvText.charCodeAt(0) === BOM_CHAR_CODE ? csvText.slice(1) : csvText
  const lines = text.split(/\r\n|\r|\n/).filter((l) => l.length > 0)
  if (lines.length === 0) return { status: 'none' }

  const header = splitCsvLine(lines[0]).map((h) => h.trim().toLowerCase())
  const poIdx = header.indexOf('po_number')
  if (poIdx === -1) return { status: 'none' }

  const distinct = new Set<string>()
  for (const line of lines.slice(1)) {
    const value = (splitCsvLine(line)[poIdx] ?? '').trim()
    if (value !== '') distinct.add(value)
  }

  if (distinct.size === 0) return { status: 'none' }
  if (distinct.size > 1) return { status: 'multiple' }
  return { status: 'single', poNumber: [...distinct][0] }
}

export function parseInwardPOPreview(csvText: string, expectedPoNumber: string): POPreviewData {
  const text = csvText.charCodeAt(0) === BOM_CHAR_CODE ? csvText.slice(1) : csvText
  const lines = text.split(/\r\n|\r|\n/).filter((l) => l.length > 0)

  if (lines.length === 0) {
    return empty('CSV file is empty or has no header row.')
  }

  const header = splitCsvLine(lines[0]).map((h) => h.trim().toLowerCase())
  const missing = REQUIRED_COLUMNS.filter((c) => !header.includes(c))
  if (missing.length > 0) {
    return empty(`Upload failed: missing required column(s): ${missing.join(', ')}`)
  }

  const dataLines = lines.slice(1)
  if (dataLines.length === 0) {
    return empty('CSV file contains no data rows.')
  }

  const poIdx = header.indexOf('po_number')
  const eanIdx = header.indexOf('ean')
  const qtyIdx = header.indexOf('ordered_qty')
  const descIdx = header.indexOf('description')
  const expected = expectedPoNumber.trim()

  const problems: string[] = []
  let mismatchedPo: string | null = null
  let totalQuantity = 0
  const allRows: POPreviewData['rows'] = []

  dataLines.forEach((line, i) => {
    const fields = splitCsvLine(line)
    const rowPo = (fields[poIdx] ?? '').trim()
    const ean = (fields[eanIdx] ?? '').trim()
    const rawQty = (fields[qtyIdx] ?? '').trim()
    const rawDesc = descIdx >= 0 ? (fields[descIdx] ?? '').trim() : ''

    if (rowPo !== expected && mismatchedPo === null) {
      mismatchedPo = rowPo
    }

    const qtyNum = Number(rawQty)
    const qtyValid = rawQty !== '' && Number.isInteger(qtyNum) && qtyNum > 0
    if (!qtyValid) {
      problems.push(`Row ${i + 1}: ordered_qty must be a positive integer.`)
    } else {
      totalQuantity += qtyNum
    }

    allRows.push({
      ean,
      description: rawDesc || null,
      ordered_qty: qtyValid ? qtyNum : 0,
    })
  })

  if (mismatchedPo !== null) {
    problems.unshift(
      `CSV contains rows for a different PO number ('${mismatchedPo}'). ` +
        `All rows must match the requested PO '${expected}'.`,
    )
  }

  return {
    rows: allRows.slice(0, 10),
    totalRows: allRows.length,
    totalQuantity,
    problems,
    isValid: problems.length === 0,
  }
}
