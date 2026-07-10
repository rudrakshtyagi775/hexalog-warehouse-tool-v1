import { apiClient } from './client'
import type {
  LoginRequest,
  LoginResponse,
  MeResponse,
  OrganisationChoiceResponse,
  SwitchOrganisationRequest,
  SwitchOrganisationResponse,
} from '@/types'

export function isOrganisationChoiceResponse(
  data: LoginResponse | OrganisationChoiceResponse,
): data is OrganisationChoiceResponse {
  return 'requires_organisation_selection' in data
}

export const authApi = {
  login: (data: LoginRequest) =>
    apiClient
      .post<LoginResponse | OrganisationChoiceResponse>('/auth/login', data)
      .then((r) => r.data),

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
