import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Boxes, ScanLine, CheckSquare, Search } from 'lucide-react'
import { inwardApi } from '@/api/inward'
import { boxHistory } from '@/lib/boxHistory'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Badge } from '@/components/ui/Badge'
import { Alert } from '@/components/ui/Alert'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, Thead, Th, Tbody, Tr, Td, EmptyRow } from '@/components/ui/Table'
import { formatDateTime } from '@/lib/utils'
import type { BoxResponse } from '@/types'

type BoxBadgeColor = 'yellow' | 'orange' | 'green'

const boxStatusBadge = (status: string): { variant: BoxBadgeColor; label: string } => {
  if (status === 'scanning') return { variant: 'yellow', label: 'Scanning' }
  if (status === 'pending_verification') return { variant: 'orange', label: 'Pending' }
  return { variant: 'green', label: 'Completed' }
}

export function ActiveBoxesPage() {
  const navigate = useNavigate()
  const [lookupId, setLookupId] = useState('')
  const [lookupError, setLookupError] = useState('')
  const [lookedUpBox, setLookedUpBox] = useState<BoxResponse | null>(null)
  const [loadingLookup, setLoadingLookup] = useState(false)
  const [history] = useState(() => boxHistory.getAll())

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault()
    const id = lookupId.trim()
    if (!id) return
    setLookupError('')
    setLookedUpBox(null)
    setLoadingLookup(true)
    try {
      const box = await inwardApi.getBox(id)
      setLookedUpBox(box)
      boxHistory.add(box.box_id, box.customer_id)
    } catch (err) {
      setLookupError(extractErrorMessage(err, 'Box not found'))
    } finally {
      setLoadingLookup(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Lookup form */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Search className="h-4 w-4 text-blue-600" />
            <CardTitle>Look Up Box</CardTitle>
          </div>
        </CardHeader>
        <form onSubmit={(e) => void handleLookup(e)} className="flex gap-3 items-end">
          <div className="flex-1">
            <Label htmlFor="lookupId">Box ID</Label>
            <Input
              id="lookupId"
              placeholder="B-TST-000001"
              value={lookupId}
              onChange={(e) => setLookupId(e.target.value)}
            />
          </div>
          <Button type="submit" loading={loadingLookup}>
            <Search className="h-4 w-4" />
            Look Up
          </Button>
        </form>
        {lookupError && (
          <Alert variant="error" className="mt-4">
            {lookupError}
          </Alert>
        )}

        {lookedUpBox && (
          <div className="mt-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
            <div className="flex items-center justify-between mb-3">
              <p className="font-mono font-bold text-gray-900">{lookedUpBox.box_id}</p>
              <Badge variant={boxStatusBadge(lookedUpBox.status).variant}>
                {boxStatusBadge(lookedUpBox.status).label}
              </Badge>
            </div>
            <div className="text-sm text-gray-600 space-y-1 mb-4">
              <p>
                Scanned: <span className="font-medium">{lookedUpBox.scanned_qty} items</span>
              </p>
              {lookedUpBox.inscan_number && (
                <p>
                  Inscan No:{' '}
                  <span className="font-medium font-mono">{lookedUpBox.inscan_number}</span>
                </p>
              )}
            </div>
            <div className="flex gap-2">
              {!lookedUpBox.is_read_only && (
                <>
                  <Button
                    size="sm"
                    onClick={() => navigate(`/scan?box=${lookedUpBox.box_id}`)}
                  >
                    <ScanLine className="h-3.5 w-3.5" />
                    Scan Items
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => navigate(`/close-box?box=${lookedUpBox.box_id}`)}
                  >
                    <CheckSquare className="h-3.5 w-3.5" />
                    Close Box
                  </Button>
                </>
              )}
            </div>
          </div>
        )}
      </Card>

      {/* Session history */}
      <Card padding={false}>
        <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Boxes className="h-4 w-4 text-gray-500" />
            <p className="text-sm font-semibold text-gray-700">Recent Boxes (this session)</p>
          </div>
          {history.length > 0 && (
            <p className="text-xs text-gray-400">
              {history.length} box{history.length !== 1 ? 'es' : ''}
            </p>
          )}
        </div>
        <Table>
          <Thead>
            <tr>
              <Th>Box ID</Th>
              <Th>Accessed</Th>
              <Th>{''}</Th>
            </tr>
          </Thead>
          <Tbody>
            {history.length === 0 ? (
              <EmptyRow cols={3} message="No boxes accessed this session." />
            ) : (
              history.map((entry) => (
                <Tr key={entry.boxId}>
                  <Td className="font-mono text-xs font-medium">{entry.boxId}</Td>
                  <Td className="text-xs text-gray-500">{formatDateTime(entry.addedAt)}</Td>
                  <Td className="text-right">
                    <div className="flex justify-end gap-1">
                      <button
                        onClick={() => navigate(`/scan?box=${entry.boxId}`)}
                        className="p-1.5 text-gray-400 hover:text-blue-600 rounded"
                        title="Scan items"
                      >
                        <ScanLine className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => navigate(`/close-box?box=${entry.boxId}`)}
                        className="p-1.5 text-gray-400 hover:text-green-600 rounded"
                        title="Close box"
                      >
                        <CheckSquare className="h-4 w-4" />
                      </button>
                    </div>
                  </Td>
                </Tr>
              ))
            )}
          </Tbody>
        </Table>
      </Card>

      <Alert variant="info">
        <strong>Note:</strong> A full active-boxes list requires a backend endpoint not yet
        implemented. Boxes are tracked locally in this browser session. Use the Look Up form above
        to find any box by ID.
      </Alert>
    </div>
  )
}
