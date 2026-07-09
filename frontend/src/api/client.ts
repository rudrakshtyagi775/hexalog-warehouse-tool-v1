import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE ?? '/api'

// ── Token store (memory only — Option A) ─────────────────────────────────────
let _accessToken: string | null = null

export const tokenStore = {
  get: () => _accessToken,
  set: (t: string | null) => { _accessToken = t },
  clear: () => { _accessToken = null },
}

// ── Axios instance ────────────────────────────────────────────────────────────
export const apiClient = axios.create({
  baseURL: BASE_URL,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStore.get()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Silent refresh on 401 — deduplicated across concurrent requests
let _refreshPromise: Promise<string> | null = null

apiClient.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    if (error.response?.status === 401 && !original._retry) {
      original._retry = true

      if (!_refreshPromise) {
        _refreshPromise = axios
          .post<{ access_token: string }>(`${BASE_URL}/auth/refresh`, {}, {
            withCredentials: true,
          })
          .then((r) => {
            tokenStore.set(r.data.access_token)
            return r.data.access_token
          })
          .finally(() => { _refreshPromise = null })
      }

      try {
        const newToken = await _refreshPromise
        original.headers.Authorization = `Bearer ${newToken}`
        return apiClient(original)
      } catch {
        tokenStore.clear()
        window.dispatchEvent(new Event('auth:logout'))
        return Promise.reject(error)
      }
    }

    return Promise.reject(error)
  },
)

export function extractErrorMessage(err: unknown, fallback = 'An error occurred'): string {
  if (axios.isAxiosError(err)) {
    return (err.response?.data as { detail?: string })?.detail ?? fallback
  }
  return fallback
}
