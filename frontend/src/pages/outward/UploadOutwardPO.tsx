import { useRef, useState } from 'react'
import { Upload, FileText, CheckCircle } from 'lucide-react'
import { useCustomers } from '@/hooks/useCustomers'
import { useUploadOutwardPO, usePreviewOutwardPO } from '@/hooks/useOutward'
import { extractErrorMessage } from '@/api/client'
import { extractPoNumberForAutofill } from '@/lib/csvPreview'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, Thead, Th, Tbody, Tr, Td, EmptyRow } from '@/components/ui/Table'
import { POPreviewModal } from '@/components/upload/POPreviewModal'
import type { OutwardPOResponse } from '@/types'

export function UploadOutwardPOPage() {
  const { data: customers = [], isLoading: customersLoading } = useCustomers({ status: 'active' })
  const uploadPO = useUploadOutwardPO()
  const previewPO = usePreviewOutwardPO()

  const [customerId, setCustomerId] = useState('')
  const [poNumber, setPoNumber] = useState('')
  const [poNumberLocked, setPoNumberLocked] = useState(false)
  const [poNumberWarning, setPoNumberWarning] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState<OutwardPOResponse | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [importSuccess, setImportSuccess] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const autofillRequestIdRef = useRef(0)

  const resetFileInput = () => {
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const closePreview = () => {
    if (uploadPO.isPending || importSuccess) return
    setPreviewOpen(false)
    setFile(null)
    previewPO.reset()
    resetFileInput()
    setPoNumber('')
    setPoNumberLocked(false)
    setPoNumberWarning('')
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0] ?? null
    if (!selected) return

    if (!customerId) {
      setError('Select a customer before choosing a file.')
      resetFileInput()
      return
    }

    // Resolve the auto-fill outcome BEFORE opening the preview or firing the
    // backend preview call, so the PO Number field is already correct in the
    // very same render that opens the modal — no post-open update, no race.
    const requestId = ++autofillRequestIdRef.current
    let autofill: ReturnType<typeof extractPoNumberForAutofill> = { status: 'none' }
    try {
      const text = await selected.text()
      autofill = extractPoNumberForAutofill(text)
    } catch {
      // Unreadable file — leave autofill as 'none'; the backend preview call
      // below still surfaces the real read error authoritatively.
    }

    // A newer file was selected while this read was in flight — abandon this one.
    if (autofillRequestIdRef.current !== requestId) return

    setError('')
    setResult(null)
    setFile(selected)
    setPreviewOpen(true)

    if (autofill.status === 'single') {
      setPoNumber(autofill.poNumber)
      setPoNumberLocked(true)
      setPoNumberWarning('')
    } else if (autofill.status === 'multiple') {
      setPoNumber('')
      setPoNumberLocked(false)
      setPoNumberWarning(
        'Multiple PO numbers found in this CSV. Please upload a single PO per file.',
      )
    } else {
      setPoNumberLocked(false)
      setPoNumberWarning('')
    }

    const fd = new FormData()
    fd.append('customer_id', customerId)
    fd.append('file', selected)
    previewPO.mutate(fd)
  }

  const handleImport = async () => {
    if (!file) return
    setError('')

    const fd = new FormData()
    fd.append('customer_id', customerId)
    fd.append('po_number', poNumber.trim())
    fd.append('file', file)

    try {
      const po = await uploadPO.mutateAsync(fd)
      setImportSuccess(true)
      setTimeout(() => {
        setResult(po)
        setPreviewOpen(false)
        setImportSuccess(false)
        setPoNumber('')
        setPoNumberLocked(false)
        setFile(null)
        previewPO.reset()
        resetFileInput()
      }, 600)
    } catch (err) {
      setError(extractErrorMessage(err))
      setPreviewOpen(false)
      setFile(null)
      setPoNumberLocked(false)
      previewPO.reset()
      resetFileInput()
    }
  }

  const totalLines = result?.lines.length ?? 0
  const totalQty = result?.lines.reduce((s, l) => s + l.ordered_qty, 0) ?? 0

  const badgeVariant = (status: string) => {
    if (status === 'open') return 'blue' as const
    return 'green' as const
  }

  const selectedCustomer = customers.find((c) => String(c.id) === customerId)
  const preview = previewPO.data

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Upload Outward PO</CardTitle>
          <p className="text-sm text-gray-500 mt-0.5">
            Import a CSV file containing outward PO lines (columns: po_number, ean, ordered_qty,
            description optional).
          </p>
        </CardHeader>

        {error && (
          <Alert
            variant={error.includes('already exists') ? 'warning' : 'error'}
            className="mb-5"
          >
            {error}
          </Alert>
        )}

        <div className="space-y-5">
          <div>
            <Label htmlFor="customer" required>
              Customer
            </Label>
            <select
              id="customer"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
              disabled={customersLoading}
              className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50"
            >
              <option value="">
                {customersLoading ? 'Loading customers…' : 'Select a customer'}
              </option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.code})
                </option>
              ))}
            </select>
          </div>

          <div>
            <Label htmlFor="poNumber" required>
              PO / Invoice Number
            </Label>
            <Input
              id="poNumber"
              placeholder="e.g. PO-2026-001"
              value={poNumber}
              onChange={(e) => setPoNumber(e.target.value)}
              readOnly={poNumberLocked}
              error={poNumberWarning || undefined}
            />
          </div>

          <div>
            <Label required>CSV File</Label>
            <label className="block cursor-pointer">
              <div
                className={[
                  'flex flex-col items-center justify-center gap-2 px-4 py-8 border-2 border-dashed rounded-lg transition-colors',
                  file
                    ? 'border-blue-400 bg-blue-50'
                    : 'border-gray-300 bg-white hover:border-gray-400',
                ].join(' ')}
              >
                {file ? (
                  <>
                    <FileText className="h-8 w-8 text-blue-500" />
                    <p className="text-sm font-medium text-blue-700">{file.name}</p>
                    <p className="text-xs text-blue-500">{(file.size / 1024).toFixed(1)} KB</p>
                  </>
                ) : (
                  <>
                    <Upload className="h-8 w-8 text-gray-400" />
                    <p className="text-sm text-gray-600">
                      <span className="font-medium text-blue-600">Click to select</span> a CSV file
                    </p>
                    <p className="text-xs text-gray-400">
                      Required columns: po_number, ean, ordered_qty
                    </p>
                  </>
                )}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(e) => void handleFileChange(e)}
              />
            </label>
          </div>
        </div>
      </Card>

      {result && (
        <Card>
          <div className="flex items-start gap-3 mb-5">
            <CheckCircle className="h-6 w-6 text-green-500 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-gray-900">Outward PO uploaded successfully</p>
              <p className="text-sm text-gray-500">
                {result.po_number} &mdash; {totalLines} line{totalLines !== 1 ? 's' : ''},{' '}
                {totalQty} units total
              </p>
            </div>
            <Badge variant={badgeVariant(result.status)} className="ml-auto">
              {result.status}
            </Badge>
          </div>

          <Table>
            <Thead>
              <tr>
                <Th>EAN</Th>
                <Th>Description</Th>
                <Th className="text-right">Ordered</Th>
                <Th className="text-right">Packed</Th>
              </tr>
            </Thead>
            <Tbody>
              {result.lines.length === 0 ? (
                <EmptyRow cols={4} message="No lines." />
              ) : (
                result.lines.map((line) => (
                  <Tr key={line.id}>
                    <Td className="font-mono text-xs">{line.ean}</Td>
                    <Td className="text-gray-500">{line.description ?? '—'}</Td>
                    <Td className="text-right">{line.ordered_qty}</Td>
                    <Td className="text-right">{line.packed_qty}</Td>
                  </Tr>
                ))
              )}
            </Tbody>
          </Table>
        </Card>
      )}

      <POPreviewModal
        open={previewOpen}
        onClose={closePreview}
        title="Outward Purchase Order Preview"
        subtitle="Please review the purchase order before importing."
        customerName={selectedCustomer?.name ?? ''}
        poNumber={poNumber}
        loading={previewPO.isPending}
        loadError={
          previewPO.isError ? extractErrorMessage(previewPO.error, 'Could not read this file.') : null
        }
        data={
          preview
            ? {
                rows: preview.first_10_rows,
                totalRows: preview.total_rows,
                totalQuantity: preview.total_quantity,
                problems: preview.problems,
                isValid: preview.is_valid,
                consolidationNotice: preview.consolidation_notice,
              }
            : null
        }
        onImport={() => void handleImport()}
        importPending={uploadPO.isPending}
        importSuccess={importSuccess}
        successMessage="Purchase order imported"
      />
    </div>
  )
}
