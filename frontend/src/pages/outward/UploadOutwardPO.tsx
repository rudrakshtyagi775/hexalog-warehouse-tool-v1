import { useRef, useState } from 'react'
import { Upload, FileText, CheckCircle } from 'lucide-react'
import { useCustomers } from '@/hooks/useCustomers'
import { useUploadOutwardPO } from '@/hooks/useOutward'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, Thead, Th, Tbody, Tr, Td, EmptyRow } from '@/components/ui/Table'
import type { OutwardPOResponse } from '@/types'

export function UploadOutwardPOPage() {
  const { data: customers = [], isLoading: customersLoading } = useCustomers({ status: 'active' })
  const uploadPO = useUploadOutwardPO()

  const [customerId, setCustomerId] = useState('')
  const [poNumber, setPoNumber] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState<OutwardPOResponse | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!customerId || !poNumber.trim() || !file) {
      setError('All fields are required.')
      return
    }
    setError('')
    setResult(null)

    const fd = new FormData()
    fd.append('customer_id', customerId)
    fd.append('po_number', poNumber.trim())
    fd.append('file', file)

    try {
      const po = await uploadPO.mutateAsync(fd)
      setResult(po)
      setPoNumber('')
      setFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  const totalLines = result?.lines.length ?? 0
  const totalQty = result?.lines.reduce((s, l) => s + l.ordered_qty, 0) ?? 0

  const badgeVariant = (status: string) => {
    if (status === 'open') return 'blue' as const
    return 'green' as const
  }

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

        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
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
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
          </div>

          <Button type="submit" loading={uploadPO.isPending} className="w-full">
            <Upload className="h-4 w-4" />
            Upload Outward PO
          </Button>
        </form>
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
    </div>
  )
}
