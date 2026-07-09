import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { customersApi } from '@/api/customers'
import type { CustomerCreate, CustomerUpdate } from '@/types'

export function useCustomers(params?: { status?: string; q?: string }) {
  return useQuery({
    queryKey: ['customers', params],
    queryFn: () => customersApi.list(params),
  })
}

export function useCustomer(id: number | null) {
  return useQuery({
    queryKey: ['customers', id],
    queryFn: () => customersApi.get(id!),
    enabled: id !== null,
  })
}

export function useCreateCustomer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: CustomerCreate) => customersApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['customers'] }),
  })
}

export function useUpdateCustomer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: CustomerUpdate }) =>
      customersApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['customers'] }),
  })
}
