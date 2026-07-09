import { apiClient } from './client'
import type {
  AuditLogListResponse,
  AdminUserResponse,
  AdminUserCreate,
  OrgResponse,
  RecentSubmissionsResponse,
  StatsResponse,
} from '@/types'

export const adminApi = {
  getStats: async (): Promise<StatsResponse> => {
    const res = await apiClient.get<StatsResponse>('/admin/stats')
    return res.data
  },

  getRecentSubmissions: async (): Promise<RecentSubmissionsResponse> => {
    const res = await apiClient.get<RecentSubmissionsResponse>('/admin/recent-submissions')
    return res.data
  },

  getAuditLogs: async (page: number, pageSize: number): Promise<AuditLogListResponse> => {
    const res = await apiClient.get<AuditLogListResponse>('/admin/audit-logs', {
      params: { page, page_size: pageSize },
    })
    return res.data
  },

  getOrg: async (): Promise<OrgResponse> => {
    const res = await apiClient.get<OrgResponse>('/admin/organisations/me')
    return res.data
  },

  listUsers: async (): Promise<AdminUserResponse[]> => {
    const res = await apiClient.get<AdminUserResponse[]>('/admin/users')
    return res.data
  },

  createUser: async (data: AdminUserCreate): Promise<AdminUserResponse> => {
    const res = await apiClient.post<AdminUserResponse>('/admin/users', data)
    return res.data
  },
}
