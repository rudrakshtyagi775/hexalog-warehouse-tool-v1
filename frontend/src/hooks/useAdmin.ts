import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { adminApi } from '@/api/admin'
import type { AdminUserCreate } from '@/types'

export function useAdminStats() {
  return useQuery({
    queryKey: ['admin-stats'],
    queryFn: adminApi.getStats,
  })
}

export function useRecentSubmissions() {
  return useQuery({
    queryKey: ['admin-recent-submissions'],
    queryFn: adminApi.getRecentSubmissions,
  })
}

export function useAuditLogs(page: number, pageSize = 20) {
  return useQuery({
    queryKey: ['admin-audit-logs', page, pageSize],
    queryFn: () => adminApi.getAuditLogs(page, pageSize),
  })
}

export function useAdminOrg() {
  return useQuery({
    queryKey: ['admin-org'],
    queryFn: adminApi.getOrg,
  })
}

export function useAdminUsers() {
  return useQuery({
    queryKey: ['admin-users'],
    queryFn: adminApi.listUsers,
  })
}

export function useCreateAdminUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AdminUserCreate) => adminApi.createUser(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  })
}
