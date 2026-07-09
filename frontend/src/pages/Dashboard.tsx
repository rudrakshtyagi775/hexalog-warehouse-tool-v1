import { useNavigate } from 'react-router-dom'
import {
  Upload,
  Package,
  ScanLine,
  Boxes,
  CheckSquare,
  Users,
  ArrowRight,
  ClipboardList,
} from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { canAccessRoute } from '@/config/permissions'

// All 5 steps are the Inward workflow — inward_operator only (PRD: admin has no
// inward permissions, packer has no inward permissions).
const workflowSteps = [
  {
    step: 1,
    title: 'Upload PO',
    desc: 'Import a purchase order CSV to register expected stock.',
    icon: Upload,
    to: '/upload-po',
  },
  {
    step: 2,
    title: 'Create Box',
    desc: 'Open a new scanning box for a customer.',
    icon: Package,
    to: '/create-box',
  },
  {
    step: 3,
    title: 'Scan Items',
    desc: 'Scan EAN barcodes into the active box.',
    icon: ScanLine,
    to: '/scan',
  },
  {
    step: 4,
    title: 'Active Boxes',
    desc: 'View and manage boxes currently being packed.',
    icon: Boxes,
    to: '/active-boxes',
  },
  {
    step: 5,
    title: 'Close Box',
    desc: 'Verify physical count and submit for inscan number.',
    icon: CheckSquare,
    to: '/close-box',
  },
]

// Admin only (PRD: manage customers / reports)
const secondaryLinks = [
  { title: 'Customers', icon: Users, to: '/customers' },
  { title: 'Reports', icon: ClipboardList, to: '/reports' },
]

export function DashboardPage() {
  const navigate = useNavigate()
  const { user, roles } = useAuth()

  const canAccess = (path: string) => canAccessRoute(roles, path)

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Welcome */}
      <div>
        <h2 className="text-2xl font-bold text-gray-900">
          Welcome back, {user?.full_name?.split(' ')[0]}
        </h2>
        <p className="text-gray-500 mt-1 text-sm">
          Hexalog Warehouse Tool — follow the workflow below to process inward stock.
        </p>
      </div>

      {/* Workflow steps */}
      <div>
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">
          Inward Workflow
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {workflowSteps.map((step, idx) => {
            const Icon = step.icon
            const accessible = canAccess(step.to)
            return (
              <button
                key={step.to}
                onClick={() => accessible && navigate(step.to)}
                disabled={!accessible}
                className={[
                  'text-left p-5 rounded-lg border transition-all',
                  accessible
                    ? 'bg-white border-gray-200 hover:border-blue-400 hover:shadow-md cursor-pointer'
                    : 'bg-gray-50 border-gray-100 opacity-50 cursor-not-allowed',
                ].join(' ')}
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className={`p-2 rounded-md ${accessible ? 'bg-blue-50' : 'bg-gray-100'}`}>
                      <Icon
                        className={`h-5 w-5 ${accessible ? 'text-blue-600' : 'text-gray-400'}`}
                      />
                    </div>
                    <span className="text-xs font-semibold text-gray-400">Step {idx + 1}</span>
                  </div>
                  {accessible && <ArrowRight className="h-4 w-4 text-gray-300 mt-1" />}
                </div>
                <p className="font-semibold text-gray-900 text-sm mb-1">{step.title}</p>
                <p className="text-xs text-gray-500 leading-relaxed">{step.desc}</p>
              </button>
            )
          })}
        </div>
      </div>

      {/* Secondary quick links — admin only, hidden entirely for other roles */}
      {secondaryLinks.filter(({ to }) => canAccess(to)).length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">
            Management
          </h3>
          <div className="flex gap-4 flex-wrap">
            {secondaryLinks
              .filter(({ to }) => canAccess(to))
              .map(({ title, icon: Icon, to }) => (
                <button
                  key={to}
                  onClick={() => navigate(to)}
                  className="flex items-center gap-2 px-4 py-2.5 bg-white border border-gray-200 rounded-lg text-sm font-medium text-gray-700 hover:border-blue-400 hover:text-blue-600 transition-colors"
                >
                  <Icon className="h-4 w-4" />
                  {title}
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}
