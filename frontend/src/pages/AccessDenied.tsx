import { useNavigate } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/Button'

export function AccessDeniedPage() {
  const navigate = useNavigate()

  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center gap-4 text-center">
      <div className="p-3 rounded-full bg-red-50">
        <ShieldAlert className="h-8 w-8 text-red-500" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Access Denied</h2>
        <p className="text-sm text-gray-500 mt-1">
          Your role does not have permission to view this page.
        </p>
      </div>
      <Button variant="secondary" onClick={() => navigate('/dashboard')}>
        Back to Dashboard
      </Button>
    </div>
  )
}
