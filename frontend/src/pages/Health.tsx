import { useCallback, useEffect, useState } from 'react'
import { Activity, CheckCircle, XCircle, AlertCircle, RefreshCw } from 'lucide-react'
import { apiClient } from '@/api/client'
import { Card, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'

type CheckStatus = 'checking' | 'ok' | 'error' | 'unknown'

interface ServiceStatus {
  name: string
  status: CheckStatus
  latency?: number
  detail?: string
  isMock?: boolean
}

function StatusIcon({ status }: { status: CheckStatus }) {
  if (status === 'ok') return <CheckCircle className="h-5 w-5 text-green-500" />
  if (status === 'error') return <XCircle className="h-5 w-5 text-red-500" />
  if (status === 'checking')
    return (
      <div className="h-5 w-5 rounded-full border-2 border-blue-400 border-t-transparent animate-spin" />
    )
  return <AlertCircle className="h-5 w-5 text-gray-400" />
}

const badgeVariant = (status: CheckStatus) => {
  if (status === 'ok') return 'green' as const
  if (status === 'error') return 'red' as const
  if (status === 'checking') return 'blue' as const
  return 'gray' as const
}

export function HealthPage() {
  const [services, setServices] = useState<ServiceStatus[]>([
    { name: 'API Server', status: 'checking' },
    {
      name: 'Database',
      status: 'unknown',
      detail: 'Not directly reachable from browser',
      isMock: true,
    },
    { name: 'Auth Service', status: 'unknown', detail: 'Checked via API server', isMock: true },
  ])
  const [checkedAt, setCheckedAt] = useState<string | null>(null)

  const runChecks = useCallback(async () => {
    setCheckedAt(null)
    setServices((prev) =>
      prev.map((s) => ({ ...s, status: s.isMock ? ('unknown' as const) : ('checking' as const) })),
    )

    const start = performance.now()
    try {
      await apiClient.get('/auth/me')
      const latency = Math.round(performance.now() - start)
      setServices((prev) =>
        prev.map((s) =>
          s.name === 'API Server'
            ? { ...s, status: 'ok' as const, latency, detail: `${latency}ms` }
            : s,
        ),
      )
    } catch {
      setServices((prev) =>
        prev.map((s) =>
          s.name === 'API Server'
            ? { ...s, status: 'error' as const, detail: 'Unreachable or auth required' }
            : s,
        ),
      )
    }

    setCheckedAt(new Date().toLocaleTimeString('en-IN'))
  }, [])

  useEffect(() => {
    void runChecks()
  }, [runChecks])

  const allOk = services.every((s) => s.status === 'ok' || s.status === 'unknown')

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <Card>
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-blue-600" />
              <CardTitle>System Health</CardTitle>
            </div>
            <Button size="sm" variant="secondary" onClick={() => void runChecks()}>
              <RefreshCw className="h-3.5 w-3.5" />
              Re-check
            </Button>
          </div>
          {checkedAt && <p className="text-xs text-gray-400 mt-1">Last checked at {checkedAt}</p>}
        </div>

        <div className="mb-4">
          <Badge variant={allOk ? 'green' : 'red'} className="text-sm px-3 py-1">
            {allOk ? 'All systems operational' : 'Degraded — check below'}
          </Badge>
        </div>

        <div className="space-y-3">
          {services.map((s) => (
            <div
              key={s.name}
              className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg border border-gray-100"
            >
              <StatusIcon status={s.status} />
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-900">{s.name}</p>
                {s.detail && <p className="text-xs text-gray-400">{s.detail}</p>}
              </div>
              <Badge variant={badgeVariant(s.status)}>
                {s.status === 'checking' ? 'Checking…' : s.status}
              </Badge>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardTitle className="mb-3">Build Info</CardTitle>
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt className="text-gray-500">Frontend</dt>
          <dd className="font-mono text-gray-700">React 18 + Vite 5</dd>
          <dt className="text-gray-500">Backend</dt>
          <dd className="font-mono text-gray-700">FastAPI + SQLAlchemy 2</dd>
          <dt className="text-gray-500">Version</dt>
          <dd className="font-mono text-gray-700">0.1.0</dd>
          <dt className="text-gray-500">Environment</dt>
          <dd>
            <Badge variant="blue">development</Badge>
          </dd>
        </dl>
      </Card>
    </div>
  )
}
