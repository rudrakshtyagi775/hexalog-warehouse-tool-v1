import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'
import { AppShell } from '@/components/layout/AppShell'
import { PageLoader } from '@/components/ui/Spinner'
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
import { OutwardScanItemsPage }   from '@/pages/outward/OutwardScanItems'
import { ActiveOutwardBoxesPage } from '@/pages/outward/ActiveOutwardBoxes'
import { CloseOutwardBoxPage }    from '@/pages/outward/CloseOutwardBox'
import { AdminUsersPage }         from '@/pages/admin/AdminUsers'
import { AuditLogsPage }          from '@/pages/admin/AuditLogs'

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
        <Route path="/upload-po"    element={<UploadPOPage />}    />
        <Route path="/create-box"   element={<CreateBoxPage />}   />
        <Route path="/scan"         element={<ScanItemsPage />}   />
        <Route path="/active-boxes" element={<ActiveBoxesPage />} />
        <Route path="/close-box"    element={<CloseBoxPage />}    />
        <Route path="/customers"    element={<CustomersPage />}   />
        <Route path="/reports"      element={<ReportsPage />}     />
        <Route path="/outward/upload-po"    element={<UploadOutwardPOPage />}    />
        <Route path="/outward/create-box"   element={<CreateOutwardBoxPage />}   />
        <Route path="/outward/scan"         element={<OutwardScanItemsPage />}   />
        <Route path="/outward/active-boxes" element={<ActiveOutwardBoxesPage />} />
        <Route path="/outward/close-box"    element={<CloseOutwardBoxPage />}    />
        <Route path="/admin/users"          element={<AdminUsersPage />}          />
        <Route path="/admin/audit-logs"     element={<AuditLogsPage />}           />
        <Route path="*"             element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  )
}
