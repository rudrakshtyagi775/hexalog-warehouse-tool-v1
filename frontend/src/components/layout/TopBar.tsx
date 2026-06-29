import { useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { Badge } from '@/components/ui/Badge'
import type { Role } from '@/types'

const routeTitles: Record<string, string> = {
  '/dashboard':    'Dashboard',
  '/upload-po':    'Upload Purchase Order',
  '/create-box':   'Create Box',
  '/scan':         'Scan Items',
  '/active-boxes': 'Active Boxes',
  '/close-box':    'Close Box',
  '/customers':    'Customers',
  '/reports':      'Reports',
  '/dev-logs':     'Development Logs',
  '/health':       'System Health',
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
