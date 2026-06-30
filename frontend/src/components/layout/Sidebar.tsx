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
  LogOut,
  Shield,
  ClipboardList,
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
  { to: '/customers', label: 'Customers', icon: Users     },
  { to: '/reports',   label: 'Reports',   icon: BarChart2 },
]

const adminNav = [
  { to: '/admin/users',      label: 'Users',      icon: Shield       },
  { to: '/admin/audit-logs', label: 'Audit Logs', icon: ClipboardList },
]

const outwardNav = [
  { to: '/outward/upload-po',    label: 'Upload Outward PO', icon: Upload      },
  { to: '/outward/create-box',   label: 'Create Box',        icon: Package     },
  { to: '/outward/scan',         label: 'Scan Items',        icon: ScanLine    },
  { to: '/outward/active-boxes', label: 'Active Boxes',      icon: Boxes       },
  { to: '/outward/close-box',    label: 'Close Box',         icon: CheckSquare },
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

function HexalogLogo() {
  return (
    <svg width="32" height="36" viewBox="0 0 46 51" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M7.65511 8.68054L0.531124 12.8892L22.545 25.9025L30.0921 21.4836L23.059 17.3109L29.4774 13.5935L36.7217 17.8372L44.6218 13.0669L22.7488 0.0540079L14.6374 4.82372L20.6156 8.35993L13.9853 12.288L7.65511 8.68054Z" fill="#8F62DF"/>
      <path d="M22.2544 27L0.492188 14L0.0276275 37.9458L22.2544 50.3257L22.2544 27Z" fill="#442A59"/>
      <path d="M45.3879 14.1025L23.0992 27.2985L23.0992 50.4686L45.3866 37.9153L45.3879 14.1025Z" fill="#442A59"/>
      <path d="M22.9922 27L45.4922 14V38L22.9922 50.5V27Z" fill="#744C8A"/>
    </svg>
  )
}

export function Sidebar() {
  const { user, organisation, logout, isAdmin } = useAuth()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col" style={{ backgroundColor: '#2D1B3D' }}>
      {/* Brand */}
      <div className="flex h-14 items-center gap-3 px-4 border-b" style={{ borderColor: '#3D2350' }}>
        <HexalogLogo />
        <div>
          <p className="text-white font-semibold text-sm leading-tight">Hexalog</p>
          <p className="text-sm" style={{ color: '#A07DE8' }}>Warehouse Tool</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-0.5">
        <p className="px-3 mb-2 text-xs font-semibold uppercase tracking-wider" style={{ color: '#744C8A' }}>
          Inward
        </p>
        {primaryNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        <div className="my-4 border-t" style={{ borderColor: '#3D2350' }} />
        <p className="px-3 mb-2 text-xs font-semibold uppercase tracking-wider" style={{ color: '#744C8A' }}>
          Outward
        </p>
        {outwardNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        <div className="my-4 border-t" style={{ borderColor: '#3D2350' }} />

        <p className="px-3 mb-2 text-xs font-semibold uppercase tracking-wider" style={{ color: '#744C8A' }}>
          Management
        </p>
        {secondaryNav.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}

        {isAdmin() && (
          <>
            <div className="my-4 border-t" style={{ borderColor: '#3D2350' }} />
            <p className="px-3 mb-2 text-xs font-semibold uppercase tracking-wider" style={{ color: '#744C8A' }}>
              Admin
            </p>
            {adminNav.map((item) => (
              <NavItem key={item.to} {...item} />
            ))}
          </>
        )}
      </nav>

      {/* User footer */}
      <div className="p-3 border-t" style={{ borderColor: '#3D2350' }}>
        <div className="px-2 mb-2">
          <p className="text-white text-sm font-medium truncate">{user?.full_name}</p>
          <p className="text-xs truncate" style={{ color: '#A07DE8' }}>
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
