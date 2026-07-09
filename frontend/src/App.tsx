import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { AppShell } from '@/components/layout/AppShell'
import { PageLoader } from '@/components/ui/Spinner'
import { canAccessRoute } from '@/config/permissions'
import { LandingPage }     from '@/pages/Landing'
import { LoginPage }       from '@/pages/Login'
import { DashboardPage }   from '@/pages/Dashboard'
import { UploadPOPage }    from '@/pages/UploadPO'
import { CreateBoxPage }   from '@/pages/CreateBox'
import { ScanItemsPage }   from '@/pages/ScanItems'
import { ActiveBoxesPage } from '@/pages/ActiveBoxes'
import { CloseBoxPage }    from '@/pages/CloseBox'
import { CustomersPage }   from '@/pages/Customers'
import { ReportsPage }     from '@/pages/Reports'
import { UploadOutwardPOPage }    from '@/pages/outward/UploadOutwardPO'
import { CreateOutwardBoxPage }   from '@/pages/outward/CreateOutwardBox'
import { CreateBoxLabelsPage }    from '@/pages/outward/CreateBoxLabels'
import { OutwardScanItemsPage }   from '@/pages/outward/OutwardScanItems'
import { ActiveOutwardBoxesPage } from '@/pages/outward/ActiveOutwardBoxes'
import { CloseOutwardBoxPage }    from '@/pages/outward/CloseOutwardBox'
import { AdminUsersPage }         from '@/pages/admin/AdminUsers'
import { AuditLogsPage }          from '@/pages/admin/AuditLogs'
import { AccessDeniedPage }       from '@/pages/AccessDenied'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) return <PageLoader />
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) return <PageLoader />
  if (isAuthenticated) return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

// Enforces the same PRD permission matrix as the backend (see config/permissions.ts).
// A user who manually navigates to a page their role can't use gets a 403 page,
// not the page itself.
function RequireRole({ children }: { children: React.ReactNode }) {
  const { roles } = useAuth()
  const { pathname } = useLocation()
  if (!canAccessRoute(roles, pathname)) return <AccessDeniedPage />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />

      {/* Protected — wrapped in AppShell */}
      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route path="/dashboard"    element={<DashboardPage />}   />
        <Route path="/upload-po"    element={<RequireRole><UploadPOPage /></RequireRole>}    />
        <Route path="/create-box"   element={<RequireRole><CreateBoxPage /></RequireRole>}   />
        <Route path="/scan"         element={<RequireRole><ScanItemsPage /></RequireRole>}   />
        <Route path="/active-boxes" element={<RequireRole><ActiveBoxesPage /></RequireRole>} />
        <Route path="/close-box"    element={<RequireRole><CloseBoxPage /></RequireRole>}    />
        <Route path="/customers"    element={<RequireRole><CustomersPage /></RequireRole>}   />
        <Route path="/reports"      element={<RequireRole><ReportsPage /></RequireRole>}     />
        <Route path="/outward/upload-po"    element={<RequireRole><UploadOutwardPOPage /></RequireRole>}    />
        <Route path="/outward/create-box"   element={<RequireRole><CreateOutwardBoxPage /></RequireRole>}   />
        <Route path="/outward/create-box-labels" element={<RequireRole><CreateBoxLabelsPage /></RequireRole>} />
        <Route path="/outward/scan"         element={<RequireRole><OutwardScanItemsPage /></RequireRole>}   />
        <Route path="/outward/active-boxes" element={<RequireRole><ActiveOutwardBoxesPage /></RequireRole>} />
        <Route path="/outward/close-box"    element={<RequireRole><CloseOutwardBoxPage /></RequireRole>}    />
        <Route path="/admin/users"          element={<RequireRole><AdminUsersPage /></RequireRole>}          />
        <Route path="/admin/audit-logs"     element={<RequireRole><AuditLogsPage /></RequireRole>}           />
        <Route path="*"             element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}
