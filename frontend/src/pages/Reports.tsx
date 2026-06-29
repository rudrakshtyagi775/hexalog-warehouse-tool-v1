import { BarChart2, Package, ScanLine, CheckSquare, TrendingUp } from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Table, Thead, Th, Tbody, Tr, Td } from '@/components/ui/Table'
import { Alert } from '@/components/ui/Alert'

const MOCK_STATS = [
  {
    label: 'Boxes Closed (This Month)',
    value: '142',
    icon: CheckSquare,
    color: 'text-green-600',
    bg: 'bg-green-50',
  },
  {
    label: 'Total Items Scanned',
    value: '3,847',
    icon: ScanLine,
    color: 'text-blue-600',
    bg: 'bg-blue-50',
  },
  {
    label: 'POs Uploaded',
    value: '38',
    icon: Package,
    color: 'text-orange-600',
    bg: 'bg-orange-50',
  },
  {
    label: 'Active Customers',
    value: '12',
    icon: TrendingUp,
    color: 'text-purple-600',
    bg: 'bg-purple-50',
  },
]

const MOCK_SUBMISSIONS = [
  { inscan: 'INS-TST-20260628-0001', customer: 'Acme Corp', boxes: 5, items: 120, date: '2026-06-28' },
  { inscan: 'INS-TST-20260627-0003', customer: 'Globex Ltd', boxes: 3, items: 74, date: '2026-06-27' },
  { inscan: 'INS-TST-20260627-0002', customer: 'Acme Corp', boxes: 8, items: 210, date: '2026-06-27' },
  { inscan: 'INS-TST-20260626-0005', customer: 'Initech Inc', boxes: 2, items: 45, date: '2026-06-26' },
  { inscan: 'INS-TST-20260625-0001', customer: 'Umbrella Co', boxes: 11, items: 280, date: '2026-06-25' },
]

export function ReportsPage() {
  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <Alert variant="info">
        Reports data shown here uses mock values. Backend reporting endpoints will be wired in a
        future milestone.
      </Alert>

      {/* Stats grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {MOCK_STATS.map(({ label, value, icon: Icon, color, bg }) => (
          <Card key={label} className="flex items-start gap-4">
            <div className={`p-2 rounded-md ${bg} flex-shrink-0`}>
              <Icon className={`h-5 w-5 ${color}`} />
            </div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{value}</p>
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
        <Table>
          <Thead>
            <tr>
              <Th>Inscan Number</Th>
              <Th>Customer</Th>
              <Th className="text-right">Boxes</Th>
              <Th className="text-right">Items</Th>
              <Th>Date</Th>
              <Th>Status</Th>
            </tr>
          </Thead>
          <Tbody>
            {MOCK_SUBMISSIONS.map((row) => (
              <Tr key={row.inscan}>
                <Td className="font-mono text-xs">{row.inscan}</Td>
                <Td className="font-medium text-gray-900">{row.customer}</Td>
                <Td className="text-right">{row.boxes}</Td>
                <Td className="text-right">{row.items}</Td>
                <Td className="text-gray-500">{row.date}</Td>
                <Td>
                  <Badge variant="green">Submitted</Badge>
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </Card>

    </div>
  )
}
