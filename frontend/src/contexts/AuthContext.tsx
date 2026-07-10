import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { tokenStore } from '@/api/client'
import { authApi, isOrganisationChoiceResponse } from '@/api/auth'
import type { LoginResponse, MeResponse, OrganisationChoiceResponse, OrganisationInfo, Role } from '@/types'

interface AuthState {
  user: MeResponse | null
  organisation: OrganisationInfo | null
  roles: Role[]
  isAuthenticated: boolean
  isLoading: boolean
}

interface AuthContextValue extends AuthState {
  login: (
    email: string,
    password: string,
    organisationId?: number,
  ) => Promise<LoginResponse | OrganisationChoiceResponse>
  logout: () => Promise<void>
  hasRole: (role: Role) => boolean
  isAdmin: () => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    organisation: null,
    roles: [],
    isAuthenticated: false,
    isLoading: true,
  })
  const initialised = useRef(false)

  const setAuthenticated = useCallback(
    (user: MeResponse, organisation: OrganisationInfo, roles: Role[]) => {
      setState({ user, organisation, roles, isAuthenticated: true, isLoading: false })
    },
    [],
  )

  const clearAuth = useCallback(() => {
    tokenStore.clear()
    setState({ user: null, organisation: null, roles: [], isAuthenticated: false, isLoading: false })
  }, [])

  // Silent refresh on mount — uses the httpOnly refresh cookie
  useEffect(() => {
    if (initialised.current) return
    initialised.current = true

    const silentRefresh = async () => {
      try {
        const { access_token } = await authApi.refresh()
        tokenStore.set(access_token)
        const me = await authApi.me()
        setAuthenticated(me, { id: me.organisation_id, name: '' }, me.roles)
      } catch {
        setState((s) => ({ ...s, isLoading: false }))
      }
    }

    silentRefresh()
  }, [setAuthenticated])

  // Listen for forced logout dispatched by the Axios interceptor
  useEffect(() => {
    const handler = () => clearAuth()
    window.addEventListener('auth:logout', handler)
    return () => window.removeEventListener('auth:logout', handler)
  }, [clearAuth])

  const login = useCallback(
    async (email: string, password: string, organisationId?: number) => {
      const res = await authApi.login({ email, password, organisation_id: organisationId })
      if (isOrganisationChoiceResponse(res)) {
        return res
      }
      tokenStore.set(res.access_token)
      setAuthenticated(
        {
          user_id: res.user.id,
          email: res.user.email,
          full_name: res.user.full_name,
          is_active: true,
          organisation_id: res.organisation.id,
          roles: res.roles,
        },
        res.organisation,
        res.roles,
      )
      return res
    },
    [setAuthenticated],
  )

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // ignore — clear local state regardless
    }
    clearAuth()
  }, [clearAuth])

  const hasRole = useCallback((role: Role) => state.roles.includes(role), [state.roles])
  const isAdmin = useCallback(() => state.roles.includes('admin'), [state.roles])

  return (
    <AuthContext.Provider value={{ ...state, login, logout, hasRole, isAdmin }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
