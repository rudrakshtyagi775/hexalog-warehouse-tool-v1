import { BarChart2, CheckSquare, Package, ScanLine, TrendingUp } from 'lucide-react'
import { useAdminStats, useRecentSubmissions } from '@/hooks/useAdmin'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Table, Thead, Th, Tbody, Tr, Td } from '@/components/ui/Table'
import { PageLoader } from '@/components/ui/Spinner'

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function ReportsPage() {
  const { data: stats, isLoading: statsLoading } = useAdminStats()
  const { data: submissionsData, isLoading: subLoading } = useRecentSubmissions()

  const statCards = stats
    ? [
        { label: 'Boxes Closed (This Month)', value: stats.inward_boxes_completed_this_month, icon: CheckSquare, color: 'text-green-600', bg: 'bg-green-50' },
        { label: 'Total Items Scanned',        value: stats.total_items_scanned,              icon: ScanLine,   color: 'text-blue-600',   bg: 'bg-blue-50'  },
        { label: 'POs Uploaded',               value: stats.total_pos_uploaded,               icon: Package,    color: 'text-orange-600', bg: 'bg-orange-50'},
        { label: 'Active Customers',           value: stats.active_customers,                  icon: TrendingUp, color: 'text-purple-600', bg: 'bg-purple-50'},
      ]
    : []

  if (statsLoading || subLoading) return <PageLoader />

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Stats grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {statCards.map(({ label, value, icon: Icon, color, bg }) => (
          <Card key={label} className="flex items-start gap-4">
            <div className={`p-2 rounded-md ${bg} flex-shrink-0`}>
              <Icon className={`h-5 w-5 ${color}`} />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{value.toLocaleString()}</p>
              <p className="text-xs text-gray-500 mt-0.5">{label}</p>
            </div>
          </Card>
        ))}
      </div>

      {/* Recent submissions */}
      <Card padding={false}>
        <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
          <BarChart2 className="h-4 w-4 text-gray-500" />
          <p className="text-sm font-semibold text-gray-700">Recent Inward Submissions</p>
        </div>
        {!submissionsData?.items.length ? (
          <p className="px-5 py-8 text-center text-sm text-gray-400">No submissions yet.</p>
        ) : (
          <Table>
            <Thead>
              <tr>
                <Th>Inscan Number</Th>
                <Th>Customer</Th>
                <Th>Box ID</Th>
                <Th className="text-right">Items</Th>
                <Th>Date</Th>
                <Th>Status</Th>
              </tr>
            </Thead>
            <Tbody>
              {submissionsData.items.map((row) => (
                <Tr key={row.inscan_number + row.box_id}>
                  <Td className="font-mono text-xs">{row.inscan_number}</Td>
                  <Td className="font-medium text-gray-900">{row.customer_name}</Td>
                  <Td className="font-mono text-xs text-gray-600">{row.box_id}</Td>
                  <Td className="text-right">{row.scanned_qty}</Td>
                  <Td className="text-gray-500">{formatDate(row.submitted_at)}</Td>
                  <Td><Badge variant="green">Submitted</Badge></Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </Card>
    </div>
  )
}
