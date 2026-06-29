import { Code2, RefreshCw } from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Alert } from '@/components/ui/Alert'
import { Table, Thead, Th, Tbody, Tr, Td } from '@/components/ui/Table'

const MOCK_LOGS = [
  { id: 1024, action: 'box_closed',      resource: 'inward_box',  resourceId: 42, user: 'Rohan Mehta',  ip: '192.168.1.10', at: '2026-06-28 14:32:05', severity: 'info'    },
  { id: 1023, action: 'scan_deleted',    resource: 'inward_scan', resourceId: 81, user: 'Rohan Mehta',  ip: '192.168.1.10', at: '2026-06-28 14:30:11', severity: 'warning' },
  { id: 1022, action: 'scan_created',    resource: 'inward_scan', resourceId: 80, user: 'Rohan Mehta',  ip: '192.168.1.10', at: '2026-06-28 14:28:44', severity: 'info'    },
  { id: 1021, action: 'box_created',     resource: 'inward_box',  resourceId: 42, user: 'Rohan Mehta',  ip: '192.168.1.10', at: '2026-06-28 14:27:01', severity: 'info'    },
  { id: 1020, action: 'po_uploaded',     resource: 'inward_po',   resourceId: 15, user: 'Arpit Sharma', ip: '192.168.1.5',  at: '2026-06-28 09:15:33', severity: 'info'    },
  { id: 1019, action: 'login',           resource: 'user',        resourceId: 3,  user: 'Arpit Sharma', ip: '192.168.1.5',  at: '2026-06-28 09:14:55', severity: 'info'    },
  { id: 1018, action: 'customer_created',resource: 'customer',    resourceId: 7,  user: 'Admin',        ip: '10.0.0.1',     at: '2026-06-27 17:02:19', severity: 'info'    },
  { id: 1017, action: 'logout_all',      resource: 'session',     resourceId: 0,  user: 'Admin',        ip: '10.0.0.1',     at: '2026-06-27 16:50:00', severity: 'warning' },
]

const severityVariant = (s: string) => (s === 'warning' ? ('yellow' as const) : ('blue' as const))

export function DevLogsPage() {
  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <Alert variant="info">
        Audit logs below use mock data. Backend{' '}
        <code>/api/admin/audit-logs</code> endpoint will be wired in a future milestone.
      </Alert>

      <Card padding={false}>
        <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Code2 className="h-4 w-4 text-gray-500" />
            <p className="text-sm font-semibold text-gray-700">Audit Log</p>
          </div>
          <button className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-blue-600">
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>
        <Table>
          <Thead>
            <tr>
              <Th>ID</Th>
              <Th>Action</Th>
              <Th>Resource</Th>
              <Th>User</Th>
              <Th>IP</Th>
              <Th>Timestamp</Th>
              <Th>Level</Th>
            </tr>
          </Thead>
          <Tbody>
            {MOCK_LOGS.map((log) => (
              <Tr key={log.id}>
                <Td className="text-xs text-gray-400">#{log.id}</Td>
                <Td className="font-mono text-xs text-gray-800">{log.action}</Td>
                <Td className="text-xs text-gray-500">
                  {log.resource}
                  {log.resourceId > 0 && (
                    <span className="text-gray-400"> #{log.resourceId}</span>
                  )}
                </Td>
                <Td className="text-sm">{log.user}</Td>
                <Td className="font-mono text-xs text-gray-400">{log.ip}</Td>
                <Td className="text-xs text-gray-500">{log.at}</Td>
                <Td>
                  <Badge variant={severityVariant(log.severity)}>{log.severity}</Badge>
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </Card>
    </div>
  )
}
