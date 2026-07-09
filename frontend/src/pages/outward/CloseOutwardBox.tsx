import { useState } from 'react'
import { CheckSquare, CheckCircle } from 'lucide-react'
import { useOutwardBox, useCloseOutwardBox } from '@/hooks/useOutward'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { PageLoader } from '@/components/ui/Spinner'
import { useSearchParams } from 'react-router-dom'

export function CloseOutwardBoxPage() {
  const [searchParams] = useSearchParams()
  const [boxIdInput, setBoxIdInput] = useState(searchParams.get('box') ?? '')
  const [activeBoxId, setActiveBoxId] = useState<string | null>(searchParams.get('box'))
  const [error, setError] = useState('')

  const { data: box, isLoading } = useOutwardBox(activeBoxId)
  const closeBox = useCloseOutwardBox()

  const handleLoadBox = (e: React.FormEvent) => {
    e.preventDefault()
    const id = boxIdInput.trim()
    if (id) setActiveBoxId(id)
  }

  const handleClose = async () => {
    if (!activeBoxId) return
    setError('')
    try {
      await closeBox.mutateAsync(activeBoxId)
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  if (box?.status === 'closed') {
    return (
      <div className="max-w-md mx-auto">
        <Card>
          <div className="text-center mb-6">
            <div className="inline-flex p-3 bg-green-100 rounded-full mb-3">
              <CheckCircle className="h-8 w-8 text-green-600" />
            </div>
            <h2 className="text-xl font-bold text-gray-900">Box Closed</h2>
            <p className="text-gray-500 text-sm mt-1">Outward box has been closed successfully.</p>
          </div>

          <div className="bg-gray-50 rounded-lg p-4 space-y-3 mb-6">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Box ID</span>
              <span className="font-mono font-bold">{box.box_id}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Items Packed</span>
              <span className="font-medium">
                {box.scans.filter((s) => s.scan_result === 'accepted').length}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Status</span>
              <Badge variant="green">Closed</Badge>
            </div>
          </div>

          <Button
            variant="secondary"
            className="w-full"
            onClick={() => {
              setActiveBoxId(null)
              setBoxIdInput('')
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
      <Card>
        <form onSubmit={handleLoadBox} className="flex gap-3 items-end">
          <div className="flex-1">
            <Label htmlFor="boxId">Box ID</Label>
            <Input
              id="boxId"
              placeholder="OB-TST-000001"
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
                  <span className="font-semibold">
                    {box.scans.filter((s) => s.scan_result === 'accepted').length}
                  </span>{' '}
                  items have been packed into this box. Close it when you are done.
                </p>
              </div>

              {error && (
                <Alert variant="error" className="mb-5">
                  {error}
                </Alert>
              )}

              <Button
                loading={closeBox.isPending}
                className="w-full"
                onClick={() => void handleClose()}
              >
                <CheckSquare className="h-4 w-4" />
                Close Box
              </Button>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
