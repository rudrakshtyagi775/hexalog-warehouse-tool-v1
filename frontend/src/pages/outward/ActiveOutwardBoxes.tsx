import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Boxes, ScanLine, CheckSquare, Search } from 'lucide-react'
import { outwardApi } from '@/api/outward'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Badge } from '@/components/ui/Badge'
import { Alert } from '@/components/ui/Alert'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, Thead, Th, Tbody, Tr, Td, EmptyRow } from '@/components/ui/Table'
import { formatDateTime } from '@/lib/utils'
import type { OutwardBoxResponse, OutwardBoxStatus } from '@/types'

type BoxBadgeColor = 'gray' | 'yellow' | 'green'

const boxStatusBadge = (status: OutwardBoxStatus): { variant: BoxBadgeColor; label: string } => {
  if (status === 'open') return { variant: 'gray', label: 'Open' }
  if (status === 'in_use') return { variant: 'yellow', label: 'In Use' }
  return { variant: 'green', label: 'Closed' }
}

const OUTWARD_BOX_HISTORY_KEY = 'outward-box-history'

interface OutwardBoxEntry {
  boxId: string
  addedAt: string
}

const outwardBoxHistory = {
  getAll(): OutwardBoxEntry[] {
    try {
      return JSON.parse(localStorage.getItem(OUTWARD_BOX_HISTORY_KEY) ?? '[]') as OutwardBoxEntry[]
    } catch {
      return []
    }
  },
  add(boxId: string) {
    const entries = outwardBoxHistory.getAll().filter((e) => e.boxId !== boxId)
    entries.unshift({ boxId, addedAt: new Date().toISOString() })
    localStorage.setItem(OUTWARD_BOX_HISTORY_KEY, JSON.stringify(entries.slice(0, 20)))
  },
}

export function ActiveOutwardBoxesPage() {
  const navigate = useNavigate()
  const [lookupId, setLookupId] = useState('')
  const [lookupError, setLookupError] = useState('')
  const [lookedUpBox, setLookedUpBox] = useState<OutwardBoxResponse | null>(null)
  const [loadingLookup, setLoadingLookup] = useState(false)
  const [history] = useState(() => outwardBoxHistory.getAll())

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault()
    const id = lookupId.trim()
    if (!id) return
    setLookupError('')
    setLookedUpBox(null)
    setLoadingLookup(true)
    try {
      const box = await outwardApi.getBox(id)
      setLookedUpBox(box)
      outwardBoxHistory.add(box.box_id)
    } catch (err) {
      setLookupError(extractErrorMessage(err, 'Box not found'))
    } finally {
      setLoadingLookup(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
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
              placeholder="OB-TST-000001"
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
                Packed:{' '}
                <span className="font-medium">
                  {lookedUpBox.scans.filter((s) => s.scan_result === 'accepted').length} items
                </span>
              </p>
            </div>
            <div className="flex gap-2">
              {!lookedUpBox.is_read_only && (
                <>
                  <Button
                    size="sm"
                    onClick={() => navigate(`/outward/scan?box=${lookedUpBox.box_id}`)}
                  >
                    <ScanLine className="h-3.5 w-3.5" />
                    Scan Items
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => navigate(`/outward/close-box?box=${lookedUpBox.box_id}`)}
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
                        onClick={() => navigate(`/outward/scan?box=${entry.boxId}`)}
                        className="p-1.5 text-gray-400 hover:text-blue-600 rounded"
                        title="Scan items"
                      >
                        <ScanLine className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => navigate(`/outward/close-box?box=${entry.boxId}`)}
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
