import { useState } from 'react'
import { Shield, ChevronLeft, ChevronRight } from 'lucide-react'
import { useAuditLogs } from '@/hooks/useAdmin'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Table, Thead, Th, Tbody, Tr, Td } from '@/components/ui/Table'
import { PageLoader } from '@/components/ui/Spinner'

const PAGE_SIZE = 20

const moduleBadge: Record<string, 'blue' | 'green' | 'orange' | 'gray'> = {
  shared: 'gray',
  inward: 'blue',
  outward: 'green',
  reports: 'orange',
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

export function AuditLogsPage() {
  const [page, setPage] = useState(1)
  const { data, isLoading } = useAuditLogs(page, PAGE_SIZE)

  if (isLoading && !data) return <PageLoader />

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  return (
    <div className="max-w-6xl mx-auto space-y-5">
      <div className="flex items-center gap-2">
        <Shield className="h-5 w-5 text-gray-600" />
        <h1 className="text-xl font-semibold text-gray-900">Audit Logs</h1>
        {data && (
          <span className="text-sm text-gray-500">({data.total.toLocaleString()} entries)</span>
        )}
      </div>

      <Card padding={false}>
        {!data?.items.length ? (
          <p className="px-5 py-8 text-center text-sm text-gray-400">No audit log entries yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <Thead>
                <tr>
                  <Th>Time</Th>
                  <Th>Module</Th>
                  <Th>Action</Th>
                  <Th>Resource</Th>
                  <Th>User</Th>
                  <Th>IP</Th>
                </tr>
              </Thead>
              <Tbody>
                {data.items.map((log) => (
                  <Tr key={log.id}>
                    <Td className="text-xs text-gray-500 whitespace-nowrap">{formatDateTime(log.created_at)}</Td>
                    <Td>
                      <Badge variant={moduleBadge[log.module] ?? 'gray'}>{log.module}</Badge>
                    </Td>
                    <Td className="font-mono text-xs">{log.action}</Td>
                    <Td className="text-sm text-gray-600">
                      {log.resource_type}
                      {log.resource_id != null && <span className="text-gray-400"> #{log.resource_id}</span>}
                    </Td>
                    <Td className="text-gray-500 text-sm">
                      {log.user_id != null ? `#${log.user_id}` : '—'}
                    </Td>
                    <Td className="font-mono text-xs text-gray-400">{log.ip_address ?? '—'}</Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </div>
        )}
      </Card>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-500">
            Page {page} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
            >
              <ChevronLeft className="h-4 w-4" />
              Previous
            </Button>
            <Button
              variant="secondary"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
