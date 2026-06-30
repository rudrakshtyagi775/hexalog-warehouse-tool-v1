import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckSquare, CheckCircle } from 'lucide-react'
import { useBox, useCloseBox, useSubmitBox } from '@/hooks/useInward'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { PageLoader } from '@/components/ui/Spinner'

export function CloseBoxPage() {
  const [searchParams] = useSearchParams()
  const [boxIdInput, setBoxIdInput] = useState(searchParams.get('box') ?? '')
  const [activeBoxId, setActiveBoxId] = useState<string | null>(searchParams.get('box'))
  const [physicalQty, setPhysicalQty] = useState('')
  const [error, setError] = useState('')

  const { data: box, isLoading } = useBox(activeBoxId)
  const closeBox = useCloseBox()
  const submitBox = useSubmitBox()

  const handleSubmit = async () => {
    if (!activeBoxId) return
    setError('')
    try {
      await submitBox.mutateAsync(activeBoxId)
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  const handleLoadBox = (e: React.FormEvent) => {
    e.preventDefault()
    const id = boxIdInput.trim()
    if (id) setActiveBoxId(id)
  }

  const handleClose = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!activeBoxId || !box) return
    const qty = parseInt(physicalQty, 10)
    if (isNaN(qty) || qty < 0) {
      setError('Enter a valid physical quantity.')
      return
    }
    setError('')

    try {
      await closeBox.mutateAsync({ boxId: activeBoxId, data: { physical_qty: qty } })
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  // Pending-verification state — box is closed, awaiting final submission
  if (box?.status === 'pending_verification') {
    return (
      <div className="max-w-md mx-auto">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CheckSquare className="h-5 w-5 text-blue-600" />
              <CardTitle>Confirm Submission</CardTitle>
            </div>
          </CardHeader>

          <div className="bg-blue-50 border border-blue-100 rounded-lg p-4 mb-5">
            <p className="text-sm text-blue-800">
              Box <span className="font-mono font-bold">{box.box_id}</span> is verified.
              Submitting will generate the Inscan Number and record inventory.
            </p>
          </div>

          <div className="bg-gray-50 rounded-lg p-4 space-y-3 mb-5">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Scanned Qty</span>
              <span className="font-medium">{box.scanned_qty}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Physical Qty</span>
              <span className="font-medium">{box.physical_qty}</span>
            </div>
          </div>

          {error && (
            <Alert variant="error" className="mb-5">
              {error}
            </Alert>
          )}

          <Button
            loading={submitBox.isPending}
            className="w-full"
            onClick={() => void handleSubmit()}
          >
            <CheckCircle className="h-4 w-4" />
            Confirm &amp; Submit
          </Button>
        </Card>
      </div>
    )
  }

  // Closed state
  if (box?.status === 'completed') {
    return (
      <div className="max-w-md mx-auto">
        <Card>
          <div className="text-center mb-6">
            <div className="inline-flex p-3 bg-green-100 rounded-full mb-3">
              <CheckCircle className="h-8 w-8 text-green-600" />
            </div>
            <h2 className="text-xl font-bold text-gray-900">Box Closed</h2>
            <p className="text-gray-500 text-sm mt-1">This box has been submitted successfully.</p>
          </div>

          <div className="bg-gray-50 rounded-lg p-4 space-y-3 mb-6">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Box ID</span>
              <span className="font-mono font-bold">{box.box_id}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Inscan Number</span>
              <span className="font-mono font-bold text-blue-700">
                {box.inscan_number ?? '—'}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Scanned Qty</span>
              <span className="font-medium">{box.scanned_qty}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Physical Qty</span>
              <span className="font-medium">{box.physical_qty}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Status</span>
              <Badge variant="green">Completed</Badge>
            </div>
          </div>

          <Button
            variant="secondary"
            className="w-full"
            onClick={() => {
              setActiveBoxId(null)
              setBoxIdInput('')
              setPhysicalQty('')
            }}
          >
            Close Another Box
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-md mx-auto space-y-5">
      {/* Box loader */}
      <Card>
        <form onSubmit={handleLoadBox} className="flex gap-3 items-end">
          <div className="flex-1">
            <Label htmlFor="boxId">Box ID</Label>
            <Input
              id="boxId"
              placeholder="B-TST-000001"
              value={boxIdInput}
              onChange={(e) => setBoxIdInput(e.target.value)}
            />
          </div>
          <Button type="submit" variant="secondary">
            Load
          </Button>
        </form>
      </Card>

      {activeBoxId && (
        <>
          {isLoading && <PageLoader />}

          {box && (
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <CheckSquare className="h-5 w-5 text-blue-600" />
                  <CardTitle>Close Box {box.box_id}</CardTitle>
                </div>
              </CardHeader>

              <div className="bg-blue-50 border border-blue-100 rounded-lg p-4 mb-5">
                <p className="text-sm text-blue-800">
                  <span className="font-semibold">{box.scanned_qty}</span> items have been scanned
                  into this box. Enter the physical count to verify before closing.
                </p>
              </div>

              {error && (
                <Alert variant="error" className="mb-5">
                  {error}
                </Alert>
              )}

              {/* Qty mismatch warning */}
              {physicalQty !== '' &&
                !isNaN(parseInt(physicalQty, 10)) &&
                parseInt(physicalQty, 10) !== box.scanned_qty && (
                  <Alert variant="warning" className="mb-5">
                    <span className="font-semibold">Quantity mismatch.</span> Scanned:{' '}
                    {box.scanned_qty}, Physical: {physicalQty}. Scanned Quantity and Physical
                    Quantity do not match. Please verify before submission.
                  </Alert>
                )}

              <form onSubmit={(e) => void handleClose(e)} className="space-y-4">
                <div>
                  <Label htmlFor="physicalQty" required>
                    Physical Count
                  </Label>
                  <Input
                    id="physicalQty"
                    type="number"
                    min={0}
                    placeholder="Enter physical item count"
                    value={physicalQty}
                    onChange={(e) => setPhysicalQty(e.target.value)}
                  />
                </div>
                <Button type="submit" loading={closeBox.isPending} className="w-full">
                  <CheckSquare className="h-4 w-4" />
                  Submit &amp; Close Box
                </Button>
              </form>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
