import { useState } from 'react'
import { Tag, Download, CheckCircle, Eye, Printer } from 'lucide-react'
import { useCustomers } from '@/hooks/useCustomers'
import { useGenerateLabels, useReprintLabel } from '@/hooks/useOutward'
import { outwardApi } from '@/api/outward'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'

function openPdfBlob(blob: Blob, filename: string, mode: 'preview' | 'download') {
  const url = URL.createObjectURL(blob)
  if (mode === 'preview') {
    window.open(url, '_blank', 'noopener,noreferrer')
    // Revoke lazily — the new tab needs the URL to still be valid once it loads.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
    return
  }
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function CreateBoxLabelsPage() {
  const { data: customers = [], isLoading: customersLoading } = useCustomers({ status: 'active' })
  const generateLabels = useGenerateLabels()
  const reprintLabel = useReprintLabel()

  const [customerId, setCustomerId] = useState('')
  const [count, setCount] = useState('1')
  const [error, setError] = useState('')
  const [boxIds, setBoxIds] = useState<string[] | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [previewing, setPreviewing] = useState(false)

  const [reprintBoxId, setReprintBoxId] = useState('')
  const [reprintError, setReprintError] = useState('')
  const [reprintSuccess, setReprintSuccess] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const parsedCount = Number(count)
    if (!customerId) {
      setError('Select a customer.')
      return
    }
    if (!Number.isInteger(parsedCount) || parsedCount < 1 || parsedCount > 50) {
      setError('Enter a number of boxes between 1 and 50.')
      return
    }
    setError('')
    setBoxIds(null)

    try {
      const result = await generateLabels.mutateAsync({
        customer_id: Number(customerId),
        count: parsedCount,
      })
      setBoxIds(result.box_ids)
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  const handleDownload = async () => {
    if (!boxIds || boxIds.length === 0) return
    setError('')
    setDownloading(true)
    try {
      const blob = await outwardApi.downloadLabelsPdf(boxIds)
      openPdfBlob(blob, 'labels.pdf', 'download')
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not download the label PDF.'))
    } finally {
      setDownloading(false)
    }
  }

  const handlePreview = async () => {
    if (!boxIds || boxIds.length === 0) return
    setError('')
    setPreviewing(true)
    try {
      const blob = await outwardApi.downloadLabelsPdf(boxIds)
      openPdfBlob(blob, 'labels.pdf', 'preview')
    } catch (err) {
      setError(extractErrorMessage(err, 'Could not preview the label PDF.'))
    } finally {
      setPreviewing(false)
    }
  }

  const handleReprint = async (e: React.FormEvent) => {
    e.preventDefault()
    const boxId = reprintBoxId.trim()
    if (!boxId) {
      setReprintError('Enter a Box ID to reprint.')
      return
    }
    setReprintError('')
    setReprintSuccess(false)
    try {
      const blob = await reprintLabel.mutateAsync(boxId)
      openPdfBlob(blob, `${boxId}.pdf`, 'download')
      setReprintSuccess(true)
    } catch (err) {
      setReprintError(extractErrorMessage(err, 'Could not reprint this Box ID.'))
    }
  }

  return (
    <div className="max-w-md mx-auto space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2 mb-1">
            <Tag className="h-5 w-5 text-blue-600" />
            <CardTitle>Create Box Labels</CardTitle>
          </div>
          <p className="text-sm text-gray-500">
            Generate sequential Box IDs and a printable label PDF for a customer.
          </p>
        </CardHeader>

        {error && (
          <Alert variant="error" className="mb-5">
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
              <option value="">{customersLoading ? 'Loading…' : 'Select a customer'}</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.code})
                </option>
              ))}
            </select>
          </div>

          <div>
            <Label htmlFor="count" required>
              Number of Boxes
            </Label>
            <Input
              id="count"
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(e.target.value)}
            />
            <p className="mt-1 text-xs text-gray-400">Between 1 and 50 boxes at a time.</p>
          </div>

          <Button type="submit" loading={generateLabels.isPending} className="w-full">
            <Tag className="h-4 w-4" />
            Generate Labels
          </Button>
        </form>
      </Card>

      {boxIds && (
        <Card>
          <div className="flex items-start gap-3 mb-4">
            <CheckCircle className="h-6 w-6 text-green-500 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-gray-900">
                {boxIds.length} box label{boxIds.length !== 1 ? 's' : ''} generated
              </p>
              <p className="text-sm text-gray-500">Download the PDF to print them.</p>
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-3 mb-4 max-h-40 overflow-y-auto space-y-1">
            {boxIds.map((id) => (
              <p key={id} className="font-mono text-xs text-gray-700">
                {id}
              </p>
            ))}
          </div>

          <div className="flex gap-3">
            <Button
              variant="secondary"
              className="flex-1"
              loading={previewing}
              onClick={() => void handlePreview()}
            >
              <Eye className="h-4 w-4" />
              Preview PDF
            </Button>
            <Button className="flex-1" loading={downloading} onClick={() => void handleDownload()}>
              <Download className="h-4 w-4" />
              Download PDF
            </Button>
          </div>
        </Card>
      )}

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2 mb-1">
            <Printer className="h-5 w-5 text-blue-600" />
            <CardTitle>Reprint a Box Label</CardTitle>
          </div>
          <p className="text-sm text-gray-500">
            Already have a Box ID? Reprint its label PDF — this increases its print count.
          </p>
        </CardHeader>

        {reprintError && (
          <Alert variant="error" className="mb-5">
            {reprintError}
          </Alert>
        )}
        {reprintSuccess && !reprintError && (
          <Alert variant="success" className="mb-5">
            Label reprinted and downloaded.
          </Alert>
        )}

        <form onSubmit={(e) => void handleReprint(e)} className="flex items-end gap-3">
          <div className="flex-1">
            <Label htmlFor="reprintBoxId" required>
              Box ID
            </Label>
            <Input
              id="reprintBoxId"
              placeholder="e.g. OB-KIR-000001"
              value={reprintBoxId}
              onChange={(e) => {
                setReprintBoxId(e.target.value)
                setReprintSuccess(false)
              }}
            />
          </div>
          <Button type="submit" loading={reprintLabel.isPending}>
            <Printer className="h-4 w-4" />
            Reprint
          </Button>
        </form>
      </Card>
    </div>
  )
}
