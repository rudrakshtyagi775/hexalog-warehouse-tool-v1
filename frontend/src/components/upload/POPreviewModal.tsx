import { CheckCircle } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Alert } from '@/components/ui/Alert'
import { Table, Thead, Th, Tbody, Tr, Td, EmptyRow } from '@/components/ui/Table'
import { Modal } from '@/components/ui/Modal'
import { Spinner } from '@/components/ui/Spinner'

export interface POPreviewRow {
  ean: string
  description: string | null
  ordered_qty: number
}

export interface POPreviewData {
  rows: POPreviewRow[]
  totalRows: number
  totalQuantity: number
  problems: string[]
  isValid: boolean
  consolidationNotice?: string | null
}

interface POPreviewModalProps {
  open: boolean
  onClose: () => void
  title: string
  subtitle: string
  customerName: string
  poNumber: string
  loading: boolean
  loadError: string | null
  data: POPreviewData | null
  onImport: () => void
  importPending: boolean
  importSuccess: boolean
  successMessage: string
}

/**
 * Shared CSV preview popup for both the Inward and Outward PO upload pages. The
 * modal itself, its animation, and its layout are identical for both flows —
 * only the data passed in (server-computed for Outward, parsed client-side for
 * Inward) differs.
 */
export function POPreviewModal({
  open,
  onClose,
  title,
  subtitle,
  customerName,
  poNumber,
  loading,
  loadError,
  data,
  onImport,
  importPending,
  importSuccess,
  successMessage,
}: POPreviewModalProps) {
  // Matches the original Outward behaviour exactly: only isPending or an invalid
  // preview disables Import — a preview load error does not (the real upload call
  // still validates authoritatively and surfaces its own error if attempted).
  const importDisabled = loading || (data ? !data.isValid : false)

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      subtitle={subtitle}
      footer={
        !importSuccess && (
          <>
            <Button variant="secondary" onClick={onClose} disabled={importPending}>
              Cancel
            </Button>
            <Button onClick={onImport} loading={importPending} disabled={importDisabled}>
              Import Purchase Order
            </Button>
          </>
        )
      }
    >
      {importSuccess ? (
        <div className="flex flex-col items-center justify-center gap-3 py-10">
          <div className="p-3 bg-green-100 rounded-full">
            <CheckCircle className="h-8 w-8 text-green-600" />
          </div>
          <p className="font-medium text-gray-900">{successMessage}</p>
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center py-16">
          <Spinner className="h-8 w-8" />
        </div>
      ) : loadError ? (
        <Alert variant="error">{loadError}</Alert>
      ) : data ? (
        <div className="space-y-5">
          {data.problems.length > 0 && (
            <Alert variant={data.isValid ? 'warning' : 'error'}>
              <ul className="list-disc list-inside space-y-0.5">
                {data.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </Alert>
          )}

          {data.consolidationNotice && <Alert variant="info">{data.consolidationNotice}</Alert>}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-500">Customer</p>
              <p className="text-sm font-semibold text-gray-900 truncate">
                {customerName || '—'}
              </p>
            </div>
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-500">PO Number</p>
              <p className="text-sm font-semibold text-gray-900 truncate">{poNumber}</p>
            </div>
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-500">Total Lines</p>
              <p className="text-sm font-semibold text-gray-900">{data.totalRows}</p>
            </div>
            <div className="bg-gray-50 rounded-lg p-3">
              <p className="text-xs text-gray-500">Total Quantity</p>
              <p className="text-sm font-semibold text-gray-900">{data.totalQuantity}</p>
            </div>
          </div>

          <div>
            {data.totalRows > data.rows.length && (
              <p className="text-xs text-gray-400 mb-2">
                Showing first {data.rows.length} of {data.totalRows} rows.
              </p>
            )}
            <div className="max-h-64 overflow-y-auto border border-gray-100 rounded-lg">
              <Table>
                <Thead>
                  <tr>
                    <Th>EAN</Th>
                    <Th>Description</Th>
                    <Th className="text-right">Ordered Quantity</Th>
                  </tr>
                </Thead>
                <Tbody>
                  {data.rows.length === 0 ? (
                    <EmptyRow cols={3} message="No rows to preview." />
                  ) : (
                    data.rows.map((row, i) => (
                      <Tr key={`${row.ean}-${i}`}>
                        <Td className="font-mono text-xs">{row.ean}</Td>
                        <Td className="text-gray-500">{row.description ?? '—'}</Td>
                        <Td className="text-right">{row.ordered_qty}</Td>
                      </Tr>
                    ))
                  )}
                </Tbody>
              </Table>
            </div>
          </div>
        </div>
      ) : null}
    </Modal>
  )
}
