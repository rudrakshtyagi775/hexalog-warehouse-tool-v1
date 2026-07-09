import { useState } from 'react'
import { Plus, Search, Users } from 'lucide-react'
import { useCustomers, useCreateCustomer, useUpdateCustomer } from '@/hooks/useCustomers'
import { useAuth } from '@/contexts/AuthContext'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Badge } from '@/components/ui/Badge'
import { Alert } from '@/components/ui/Alert'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, Thead, Th, Tbody, Tr, Td, EmptyRow } from '@/components/ui/Table'
import type { CustomerListItem } from '@/types'

function CreateCustomerForm({ onClose }: { onClose: () => void }) {
  const create = useCreateCustomer()
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !code.trim()) {
      setError('Name and code are required.')
      return
    }
    setError('')
    try {
      await create.mutateAsync({ name: name.trim(), code: code.trim().toUpperCase() })
      onClose()
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>New Customer</CardTitle>
      </CardHeader>
      {error && (
        <Alert variant="error" className="mb-4">
          {error}
        </Alert>
      )}
      <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label htmlFor="name" required>
              Name
            </Label>
            <Input
              id="name"
              placeholder="Customer name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="code" required>
              Code
            </Label>
            <Input
              id="code"
              placeholder="TST"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              maxLength={10}
            />
          </div>
        </div>
        <div className="flex gap-2">
          <Button type="submit" loading={create.isPending}>
            Create Customer
          </Button>
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  )
}

export function CustomersPage() {
  const { isAdmin } = useAuth()
  const [q, setQ] = useState('')
  const [statusFilter, setStatusFilter] = useState<'active' | 'inactive' | ''>('')
  const [showCreate, setShowCreate] = useState(false)
  const [togglingId, setTogglingId] = useState<number | null>(null)

  const {
    data: customers = [],
    isLoading,
    error,
  } = useCustomers({
    q: q || undefined,
    status: statusFilter || undefined,
  })
  const update = useUpdateCustomer()

  const handleToggleStatus = async (c: CustomerListItem) => {
    setTogglingId(c.id)
    try {
      await update.mutateAsync({
        id: c.id,
        data: { status: c.status === 'active' ? 'inactive' : 'active' },
      })
    } finally {
      setTogglingId(null)
    }
  }

  const adminMode = isAdmin()

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      {showCreate && adminMode && (
        <CreateCustomerForm onClose={() => setShowCreate(false)} />
      )}

      <Card padding={false}>
        {/* Toolbar */}
        <div className="px-5 py-3 border-b border-gray-100 flex flex-wrap gap-3 items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-gray-500" />
            <p className="text-sm font-semibold text-gray-700">Customers</p>
          </div>
          <div className="flex gap-2 flex-wrap">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400" />
              <input
                placeholder="Search name…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                className="pl-7 pr-3 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 w-44"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              className="px-2 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
            {adminMode && (
              <Button size="sm" onClick={() => setShowCreate((v) => !v)}>
                <Plus className="h-3.5 w-3.5" />
                New Customer
              </Button>
            )}
          </div>
        </div>

        {error && (
          <Alert variant="error" className="m-4">
            {String(error)}
          </Alert>
        )}

        <Table>
          <Thead>
            <tr>
              <Th>Name</Th>
              <Th>Code</Th>
              <Th>Status</Th>
              {adminMode && <Th className="text-right">Actions</Th>}
            </tr>
          </Thead>
          <Tbody>
            {isLoading ? (
              <tr>
                <td colSpan={adminMode ? 4 : 3} className="px-4 py-8 text-center text-sm text-gray-400">
                  Loading…
                </td>
              </tr>
            ) : customers.length === 0 ? (
              <EmptyRow cols={adminMode ? 4 : 3} message="No customers found." />
            ) : (
              customers.map((c) => (
                <Tr key={c.id}>
                  <Td className="font-medium text-gray-900">{c.name}</Td>
                  <Td className="font-mono text-xs text-gray-600">{c.code}</Td>
                  <Td>
                    <Badge variant={c.status === 'active' ? 'green' : 'gray'}>{c.status}</Badge>
                  </Td>
                  {adminMode && (
                    <Td className="text-right">
                      <button
                        onClick={() => void handleToggleStatus(c)}
                        disabled={togglingId === c.id}
                        className="text-xs text-blue-600 hover:underline disabled:opacity-50"
                      >
                        {togglingId === c.id
                          ? 'Saving…'
                          : c.status === 'active'
                            ? 'Deactivate'
                            : 'Activate'}
                      </button>
                    </Td>
                  )}
                </Tr>
              ))
            )}
          </Tbody>
        </Table>
      </Card>
    </div>
  )
}
