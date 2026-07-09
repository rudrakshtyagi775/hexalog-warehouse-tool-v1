import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ScanLine, Trash2, Package } from 'lucide-react'
import { useBox, useAddScan, useDeleteScan } from '@/hooks/useInward'
import { boxHistory } from '@/lib/boxHistory'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, Thead, Th, Tbody, Tr, Td, EmptyRow } from '@/components/ui/Table'
import { PageLoader } from '@/components/ui/Spinner'
import { formatDateTime } from '@/lib/utils'
type BadgeColor = 'yellow' | 'orange' | 'gray'

const statusBadge = (status: string): { variant: BadgeColor; label: string } => {
  if (status === 'scanning') return { variant: 'yellow', label: 'Scanning' }
  if (status === 'pending_verification') return { variant: 'orange', label: 'Pending Verification' }
  return { variant: 'gray', label: 'Completed' }
}

export function ScanItemsPage() {
  const [searchParams] = useSearchParams()
  const [boxIdInput, setBoxIdInput] = useState(searchParams.get('box') ?? '')
  const [activeBoxId, setActiveBoxId] = useState<string | null>(searchParams.get('box'))
  const [eanInput, setEanInput] = useState('')
  const [scanError, setScanError] = useState('')
  const [lastNote, setLastNote] = useState<string | null>(null)

  const scanInputRef = useRef<HTMLInputElement>(null)
  const addScan = useAddScan()
  const deleteScan = useDeleteScan()
  const { data: box, isLoading: boxLoading } = useBox(activeBoxId)

  useEffect(() => {
    if (box && !box.is_read_only) {
      scanInputRef.current?.focus()
    }
  }, [box?.box_id, box?.is_read_only])

  const handleBoxLoad = (e: React.FormEvent) => {
    e.preventDefault()
    const id = boxIdInput.trim()
    if (!id) return
    setScanError('')
    setActiveBoxId(id)
    boxHistory.add(id, 0)
  }

  const handleScan = async () => {
    const ean = eanInput.trim()
    if (!ean || !activeBoxId) return
    setScanError('')
    setLastNote(null)

    try {
      const result = await addScan.mutateAsync({ boxId: activeBoxId, data: { ean } })
      setEanInput('')
      if (result.note) setLastNote(result.note)
      scanInputRef.current?.focus()
    } catch (err) {
      setScanError(extractErrorMessage(err))
      setEanInput('')
      scanInputRef.current?.focus()
    }
  }

  const handleEanKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      void handleScan()
    }
  }

  const handleDelete = async (scanId: number) => {
    if (!activeBoxId) return
    try {
      await deleteScan.mutateAsync({ scanId, boxId: activeBoxId })
    } catch (err) {
      setScanError(extractErrorMessage(err))
    }
  }

  const activeScans = box?.scans.filter((s) => !s.is_deleted) ?? []
  const badge = box ? statusBadge(box.status) : null

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      {/* Box loader */}
      <Card>
        <form onSubmit={handleBoxLoad} className="flex gap-3 items-end">
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
            Load Box
          </Button>
        </form>
      </Card>

      {/* Box details + scan */}
      {activeBoxId && (
        <>
          {boxLoading && <PageLoader />}

          {box && (
            <>
              {/* Box header */}
              <Card>
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-50 rounded-md">
                      <Package className="h-5 w-5 text-blue-600" />
                    </div>
                    <div>
                      <p className="font-mono font-bold text-gray-900">{box.box_id}</p>
                      <p className="text-xs text-gray-500">
                        {activeScans.length} item{activeScans.length !== 1 ? 's' : ''} scanned
                      </p>
                    </div>
                  </div>
                  <Badge variant={badge!.variant}>{badge!.label}</Badge>
                </div>
              </Card>

              {/* Scan input — only when not read-only */}
              {box.is_read_only ? (
                <Alert variant="info">
                  This box is closed. Showing details in read-only mode.
                </Alert>
              ) : (
                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <ScanLine className="h-4 w-4 text-blue-600" />
                      <CardTitle>Scan EAN Barcode</CardTitle>
                    </div>
                  </CardHeader>

                  {scanError && (
                    <Alert variant="error" className="mb-4">
                      {scanError}
                    </Alert>
                  )}
                  {lastNote && (
                    <Alert variant="warning" className="mb-4">
                      {lastNote}
                    </Alert>
                  )}

                  <div className="flex gap-3">
                    <Input
                      ref={scanInputRef}
                      placeholder="Scan or type EAN barcode…"
                      value={eanInput}
                      onChange={(e) => setEanInput(e.target.value)}
                      onKeyDown={handleEanKeyDown}
                      disabled={addScan.isPending}
                      className="font-mono text-base"
                    />
                    <Button
                      onClick={() => void handleScan()}
                      loading={addScan.isPending}
                      disabled={!eanInput.trim()}
                    >
                      Add
                    </Button>
                  </div>
                  <p className="mt-2 text-xs text-gray-400">
                    Press Enter after scanning. Barcode scanners work automatically.
                  </p>
                </Card>
              )}

              {/* Scan list */}
              <Card padding={false}>
                <div className="px-5 py-3 border-b border-gray-100">
                  <p className="text-sm font-semibold text-gray-700">
                    Scans ({activeScans.length})
                  </p>
                </div>
                <Table>
                  <Thead>
                    <tr>
                      <Th>#</Th>
                      <Th>EAN</Th>
                      <Th>Scanned at</Th>
                      {!box.is_read_only && <Th>{''}</Th>}
                    </tr>
                  </Thead>
                  <Tbody>
                    {activeScans.length === 0 ? (
                      <EmptyRow cols={box.is_read_only ? 3 : 4} message="No scans yet." />
                    ) : (
                      [...activeScans].reverse().map((scan, i) => (
                        <Tr key={scan.id}>
                          <Td className="text-gray-400 text-xs w-10">
                            {activeScans.length - i}
                          </Td>
                          <Td className="font-mono text-xs">{scan.ean}</Td>
                          <Td className="text-xs text-gray-500">
                            {formatDateTime(scan.created_at.toString())}
                          </Td>
                          {!box.is_read_only && (
                            <Td className="w-10 text-right">
                              <button
                                onClick={() => void handleDelete(scan.id)}
                                className="p-1 text-gray-300 hover:text-red-500 transition-colors rounded"
                                title="Delete scan"
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            </Td>
                          )}
                        </Tr>
                      ))
                    )}
                  </Tbody>
                </Table>
              </Card>
            </>
          )}
        </>
      )}
    </div>
  )
}
