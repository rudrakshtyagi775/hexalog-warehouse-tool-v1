import { useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { Badge } from '@/components/ui/Badge'
import type { Role } from '@/types'

const routeTitles: Record<string, string> = {
  '/dashboard':             'Dashboard',
  '/upload-po':             'Upload Inward PO',
  '/create-box':            'Create Inward Box',
  '/scan':                  'Scan Inward Items',
  '/active-boxes':          'Active Inward Boxes',
  '/close-box':             'Close Inward Box',
  '/customers':             'Customers',
  '/reports':               'Reports',
  '/outward/upload-po':     'Upload Outward PO',
  '/outward/create-box':    'Create Outward Box',
  '/outward/scan':          'Scan Outward Items',
  '/outward/active-boxes':  'Active Outward Boxes',
  '/outward/close-box':     'Close Outward Box',
  '/admin/users':           'Admin — Users',
  '/admin/audit-logs':      'Admin — Audit Logs',
}

const roleBadgeVariant = (role: Role) => {
  if (role === 'admin') return 'blue'
  if (role === 'packer') return 'green'
  return 'orange'
}

export function TopBar() {
  const { pathname } = useLocation()
  const { roles } = useAuth()
  const title = routeTitles[pathname] ?? 'Hexalog Warehouse'

  return (
    <header className="fixed top-0 left-60 right-0 z-20 h-14 bg-white border-b border-gray-200 flex items-center px-6 gap-4">
      <h1 className="text-base font-semibold text-gray-900 flex-1">{title}</h1>
      <div className="flex items-center gap-2">
        {roles.map((r) => (
          <Badge key={r} variant={roleBadgeVariant(r)}>
            {r.replace('_', ' ')}
          </Badge>
        ))}
      </div>
    </header>
  )
}
