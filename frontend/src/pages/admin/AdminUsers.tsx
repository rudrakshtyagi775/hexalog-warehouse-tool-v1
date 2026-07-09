import { useState } from 'react'
import { UserPlus, Shield } from 'lucide-react'
import { useAdminUsers, useCreateAdminUser } from '@/hooks/useAdmin'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Table, Thead, Th, Tbody, Tr, Td } from '@/components/ui/Table'
import { PageLoader } from '@/components/ui/Spinner'
import type { Role } from '@/types'

const ALL_ROLES: Role[] = ['admin', 'inward_operator', 'packer']

const roleBadgeVariant: Record<Role, 'red' | 'blue' | 'green'> = {
  admin: 'red',
  inward_operator: 'blue',
  packer: 'green',
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function AdminUsersPage() {
  const { data: users = [], isLoading } = useAdminUsers()
  const createUser = useCreateAdminUser()

  const [showForm, setShowForm] = useState(false)
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [selectedRoles, setSelectedRoles] = useState<Role[]>(['packer'])
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const toggleRole = (role: Role) => {
    setSelectedRoles((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedRoles.length) {
      setError('Select at least one role.')
      return
    }
    setError('')
    setSuccess('')
    try {
      const user = await createUser.mutateAsync({ email, full_name: fullName, password, roles: selectedRoles })
      setSuccess(`User ${user.email} created.`)
      setEmail('')
      setFullName('')
      setPassword('')
      setSelectedRoles(['packer'])
      setShowForm(false)
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  if (isLoading) return <PageLoader />

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Users</h1>
        <Button onClick={() => { setShowForm((v) => !v); setError(''); setSuccess('') }}>
          <UserPlus className="h-4 w-4" />
          {showForm ? 'Cancel' : 'Add User'}
        </Button>
      </div>

      {success && <Alert variant="success">{success}</Alert>}

      {showForm && (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Shield className="h-5 w-5 text-blue-600" />
              <CardTitle>Create New User</CardTitle>
            </div>
          </CardHeader>

          {error && <Alert variant="error" className="mb-4">{error}</Alert>}

          <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
            <div>
              <Label htmlFor="fullName" required>Full Name</Label>
              <Input
                id="fullName"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Jane Smith"
                required
              />
            </div>
            <div>
              <Label htmlFor="email" required>Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="jane@example.com"
                required
              />
            </div>
            <div>
              <Label htmlFor="password" required>Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Min 8 characters"
                required
                minLength={8}
              />
            </div>
            <div>
              <Label required>Roles</Label>
              <div className="flex gap-3 mt-1">
                {ALL_ROLES.map((role) => (
                  <label key={role} className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={selectedRoles.includes(role)}
                      onChange={() => toggleRole(role)}
                      className="rounded"
                    />
                    <span className="text-sm capitalize">{role.replace('_', ' ')}</span>
                  </label>
                ))}
              </div>
            </div>
            <Button type="submit" loading={createUser.isPending}>
              Create User
            </Button>
          </form>
        </Card>
      )}

      <Card padding={false}>
        {!users.length ? (
          <p className="px-5 py-8 text-center text-sm text-gray-400">No users found.</p>
        ) : (
          <Table>
            <Thead>
              <tr>
                <Th>Name</Th>
                <Th>Email</Th>
                <Th>Roles</Th>
                <Th>Status</Th>
                <Th>Added</Th>
              </tr>
            </Thead>
            <Tbody>
              {users.map((u) => (
                <Tr key={u.id}>
                  <Td className="font-medium text-gray-900">{u.full_name}</Td>
                  <Td className="text-gray-600 text-sm">{u.email}</Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {u.roles.map((r) => (
                        <Badge key={r.role} variant={roleBadgeVariant[r.role]}>
                          {r.role.replace('_', ' ')}
                        </Badge>
                      ))}
                    </div>
                  </Td>
                  <Td>
                    <Badge variant={u.is_active ? 'green' : 'gray'}>
                      {u.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  </Td>
                  <Td className="text-gray-500">{formatDate(u.created_at)}</Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        )}
      </Card>
    </div>
  )
}
