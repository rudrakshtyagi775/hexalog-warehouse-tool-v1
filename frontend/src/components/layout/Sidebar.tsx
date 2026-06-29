import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  Upload,
  Package,
  ScanLine,
  Boxes,
  CheckSquare,
  Users,
  BarChart2,
  Code2,
  Activity,
  LogOut,
  Warehouse,
} from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { cn } from '@/lib/utils'

const primaryNav = [
  { to: '/dashboard',    label: 'Dashboard',    icon: LayoutDashboard },
  { to: '/upload-po',    label: 'Upload PO',    icon: Upload          },
  { to: '/create-box',   label: 'Create Box',   icon: Package         },
  { to: '/scan',         label: 'Scan Items',   icon: ScanLine        },
  { to: '/active-boxes', label: 'Active Boxes', icon: Boxes           },
  { to: '/close-box',    label: 'Close Box',    icon: CheckSquare     },
]

const secondaryNav = [
  { to: '/customers', label: 'Customers',   icon: Users     },
  { to: '/reports',   label: 'Reports',     icon: BarChart2 },
  { to: '/dev-logs',  label: 'Dev Logs',    icon: Code2     },
  { to: '/health',    label: 'Health',      icon: Activity  },
]

function NavItem({
  to,
  label,
  icon: Icon,
}: {
  to: string
  label: string
  icon: React.ElementType
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn('sidebar-link', isActive ? 'sidebar-link-active' : 'sidebar-link-inactive')
      }
    >
      <Icon className="h-4 w-4 flex-shrink-0" />
      <span>{label}</span>
    </NavLink>
  )
}

export function Sidebar() {
  const { user, organisation, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col bg-slate-900">
      {/* Brand */}
      <div className="flex h-14 items-center gap-2.5 px-4 border-b border-slate-800">
        <div className="p-1.5 bg-blue-600 rounded-md flex-shrink-0">
          <Warehouse className="h-5 w-5 text-white" />
        </div>
        <div>
          <p className="text-white font-semibold text-sm leading-tight">Hexalog</p>
          <p className="text-slate-400 text-xs">Warehouse Tool</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-0.5">
        <p className="px-3 mb-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">
          Workflow
        </p>
        {primaryNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        <div className="my-4 border-t border-slate-800" />

        <p className="px-3 mb-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">
          Management
        </p>
        {secondaryNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}
      </nav>

      {/* User footer */}
      <div className="border-t border-slate-800 p-3">
        <div className="px-2 mb-2">
          <p className="text-white text-sm font-medium truncate">{user?.full_name}</p>
          <p className="text-slate-400 text-xs truncate">
            {organisation?.name || user?.email}
          </p>
        </div>
        <button
          onClick={handleLogout}
          className="w-full sidebar-link sidebar-link-inactive"
        >
          <LogOut className="h-4 w-4" />
          <span>Sign out</span>
        </button>
      </div>
    </aside>
  )
}
