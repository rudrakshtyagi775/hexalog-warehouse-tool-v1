import { useEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { useSearchParams } from 'react-router-dom'
import { ScanLine, Trash2, Package } from 'lucide-react'
import { useOutwardBox, useAddOutwardScan, useDeleteOutwardScan } from '@/hooks/useOutward'
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
import type { OutwardBoxStatus } from '@/types'

type BadgeColor = 'yellow' | 'gray' | 'green'

const outwardBoxHistory = {
  add(boxId: string) {
    try {
      const KEY = 'outward-box-history'
      const raw = localStorage.getItem(KEY)
      const entries: Array<{ boxId: string; addedAt: string }> = raw
        ? (JSON.parse(raw) as Array<{ boxId: string; addedAt: string }>)
        : []
      const filtered = entries.filter((e) => e.boxId !== boxId)
      filtered.unshift({ boxId, addedAt: new Date().toISOString() })
      localStorage.setItem(KEY, JSON.stringify(filtered.slice(0, 20)))
    } catch { /* ignore */ }
  },
}

const statusBadge = (status: OutwardBoxStatus): { variant: BadgeColor; label: string } => {
  if (status === 'open') return { variant: 'gray', label: 'Open' }
  if (status === 'in_use') return { variant: 'yellow', label: 'In Use' }
  return { variant: 'green', label: 'Closed' }
}

export function OutwardScanItemsPage() {
  const [searchParams] = useSearchParams()
  const [boxIdInput, setBoxIdInput] = useState(searchParams.get('box') ?? '')
  const [activeBoxId, setActiveBoxId] = useState<string | null>(searchParams.get('box'))
  const [eanInput, setEanInput] = useState('')
  const [scanError, setScanError] = useState('')
  const [lastNote, setLastNote] = useState<string | null>(null)

  const scanInputRef = useRef<HTMLInputElement>(null)
  // Barcode scanners type a full EAN in a fast, uninterrupted burst with no
  // reliable terminator we can require. AUTO_SUBMIT_QUIET_MS is how long the
  // input must sit still after the last keystroke before we treat the burst
  // as finished and submit — long enough to never split a real scan (which
  // completes in well under this window), short enough to feel instant.
  const AUTO_SUBMIT_QUIET_MS = 75
  const autoSubmitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const addScan = useAddOutwardScan()
  const deleteScan = useDeleteOutwardScan()
  const { data: box, isLoading: boxLoading } = useOutwardBox(activeBoxId)

  useEffect(() => {
    if (box && !box.is_read_only) {
      scanInputRef.current?.focus()
    }
  }, [box?.box_id, box?.is_read_only])

  useEffect(() => {
    return () => {
      if (autoSubmitTimerRef.current) clearTimeout(autoSubmitTimerRef.current)
    }
  }, [])

  const clearAutoSubmitTimer = () => {
    if (autoSubmitTimerRef.current) {
      clearTimeout(autoSubmitTimerRef.current)
      autoSubmitTimerRef.current = null
    }
  }

  const handleBoxLoad = (e: React.FormEvent) => {
    e.preventDefault()
    const id = boxIdInput.trim()
    if (!id) return
    setScanError('')
    setActiveBoxId(id)
    outwardBoxHistory.add(id)
  }

  const handleScan = async (eanOverride?: string) => {
    clearAutoSubmitTimer()
    if (addScan.isPending) return // one in-flight scan at a time; ignore extra Enter/Add/auto triggers
    // eanOverride is used by the auto-submit timer: it's scheduled from an
    // onChange closure whose `eanInput` state hasn't caught up to the just-typed
    // character yet, so the live DOM value is passed explicitly to avoid
    // submitting a barcode that's one character short.
    const ean = (eanOverride ?? eanInput).trim()
    if (!ean || !activeBoxId) return
    setScanError('')
    setLastNote(null)

    try {
      const result = await addScan.mutateAsync({ boxId: activeBoxId, data: { ean } })
      // flushSync forces React to commit the disabled->enabled DOM update (driven
      // by addScan.isPending flipping to false) before we call .focus() below —
      // without it, the input can still be `disabled` in the DOM at focus time
      // and the browser silently drops the focus call.
      flushSync(() => {
        setEanInput('')
        if (result.note) setLastNote(result.note)
      })
      scanInputRef.current?.focus()
    } catch (err) {
      flushSync(() => {
        setScanError(extractErrorMessage(err))
        setEanInput('')
      })
      scanInputRef.current?.focus()
    }
  }

  const handleEanChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setEanInput(value)
    clearAutoSubmitTimer()
    if (value.trim()) {
      // No terminator required: once the scanner's burst of characters goes
      // quiet for AUTO_SUBMIT_QUIET_MS, treat the barcode as complete. `value`
      // (the live DOM value) is passed explicitly — see handleScan's comment.
      autoSubmitTimerRef.current = setTimeout(() => void handleScan(value), AUTO_SUBMIT_QUIET_MS)
    }
  }

  const handleEanKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      void handleScan() // manual fallback; clears the pending auto-submit timer itself
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

  const activeScans = box?.scans.filter((s) => s.scan_result === 'accepted') ?? []
  const badge = box ? statusBadge(box.status) : null

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <Card>
        <form onSubmit={handleBoxLoad} className="flex gap-3 items-end">
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
            Load Box
          </Button>
        </form>
      </Card>

      {activeBoxId && (
        <>
          {boxLoading && <PageLoader />}

          {box && (
            <>
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
                      onChange={handleEanChange}
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
                    Just scan — it submits automatically. No need to press Enter or click Add.
                  </p>
                </Card>
              )}

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
