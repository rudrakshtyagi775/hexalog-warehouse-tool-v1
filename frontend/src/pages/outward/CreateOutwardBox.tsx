import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Package, ArrowRight, CheckCircle } from 'lucide-react'
import { useCustomers } from '@/hooks/useCustomers'
import { useCreateOutwardBox } from '@/hooks/useOutward'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { Card, CardHeader, CardTitle } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import type { OutwardBoxResponse } from '@/types'

export function CreateOutwardBoxPage() {
  const navigate = useNavigate()
  const { data: customers = [], isLoading: customersLoading } = useCustomers({ status: 'active' })
  const createBox = useCreateOutwardBox()

  const [customerId, setCustomerId] = useState('')
  const [error, setError] = useState('')
  const [box, setBox] = useState<OutwardBoxResponse | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!customerId) {
      setError('Select a customer.')
      return
    }
    setError('')

    try {
      const created = await createBox.mutateAsync({ customer_id: Number(customerId) })
      setBox(created)
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }

  if (box) {
    const customer = customers.find((c) => c.id === box.customer_id)
    return (
      <div className="max-w-md mx-auto">
        <Card>
          <div className="text-center mb-6">
            <div className="inline-flex p-3 bg-green-100 rounded-full mb-3">
              <CheckCircle className="h-8 w-8 text-green-600" />
            </div>
            <h2 className="text-xl font-bold text-gray-900">Box Created</h2>
            <p className="text-gray-500 text-sm mt-1">Ready to scan outward items into this box.</p>
          </div>

          <div className="bg-gray-50 rounded-lg p-4 mb-6 space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Box ID</span>
              <span className="font-mono font-bold text-gray-900 text-base">{box.box_id}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Customer</span>
              <span className="font-medium text-gray-900">
                {customer?.name ?? String(box.customer_id)}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Status</span>
              <Badge variant="gray">Open</Badge>
            </div>
          </div>

          <div className="space-y-3">
            <Button className="w-full" onClick={() => navigate(`/outward/scan?box=${box.box_id}`)}>
              <ArrowRight className="h-4 w-4" />
              Start Scanning Items
            </Button>
            <Button
              variant="secondary"
              className="w-full"
              onClick={() => {
                setBox(null)
                setCustomerId('')
              }}
            >
              Create Another Box
            </Button>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-md mx-auto">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2 mb-1">
            <Package className="h-5 w-5 text-blue-600" />
            <CardTitle>Create Outward Box</CardTitle>
          </div>
          <p className="text-sm text-gray-500">
            Open a new outward box for a customer.
          </p>
        </CardHeader>

        {error && (
          <Alert variant="error" className="mb-5">
            {error}
          </Alert>
        )}

        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-5">
          <div>
            <Label htmlFor="customer" required>
              Customer
            </Label>
            <select
              id="customer"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
              disabled={customersLoading}
              className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50"
            >
              <option value="">{customersLoading ? 'Loading…' : 'Select a customer'}</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.code})
                </option>
              ))}
            </select>
          </div>

          <Button type="submit" loading={createBox.isPending} className="w-full">
            <Package className="h-4 w-4" />
            Create Box
          </Button>
        </form>
      </Card>
    </div>
  )
}
