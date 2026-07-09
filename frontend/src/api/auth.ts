import { apiClient } from './client'
import type {
  LoginRequest,
  LoginResponse,
  MeResponse,
  SwitchOrganisationRequest,
  SwitchOrganisationResponse,
} from '@/types'

export const authApi = {
  login: (data: LoginRequest) =>
    apiClient.post<LoginResponse>('/auth/login', data).then((r) => r.data),

  refresh: () =>
    apiClient.post<{ access_token: string }>('/auth/refresh').then((r) => r.data),

  logout: () =>
    apiClient.post('/auth/logout'),

  logoutAll: () =>
    apiClient.post('/auth/logout-all'),

  me: () =>
    apiClient.get<MeResponse>('/auth/me').then((r) => r.data),

  switchOrganisation: (data: SwitchOrganisationRequest) =>
    apiClient
      .post<SwitchOrganisationResponse>('/auth/switch-organisation', data)
      .then((r) => r.data),
}
