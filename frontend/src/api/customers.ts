import { apiClient } from './client'
import type { CustomerCreate, CustomerListItem, CustomerResponse, CustomerUpdate } from '@/types'

export const customersApi = {
  list: (params?: { status?: string; q?: string }) =>
    apiClient.get<CustomerListItem[]>('/customers', { params }).then((r) => r.data),

  get: (id: number) =>
    apiClient.get<CustomerResponse>(`/customers/${id}`).then((r) => r.data),

  create: (data: CustomerCreate) =>
    apiClient.post<CustomerResponse>('/customers', data).then((r) => r.data),

  update: (id: number, data: CustomerUpdate) =>
    apiClient.patch<CustomerResponse>(`/customers/${id}`, data).then((r) => r.data),
}
