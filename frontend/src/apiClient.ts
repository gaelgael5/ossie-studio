import { ApiRequestError, type ApiErrorBody } from './errors'

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (response.status === 204) return undefined as T
  const text = await response.text()
  const body = text ? JSON.parse(text) : {}
  if (!response.ok) throw new ApiRequestError(body as ApiErrorBody, response.status)
  return body as T
}

export interface SessionUser {
  email: string
  role: string
}
export interface SessionState {
  bootstrap_required: boolean
  authenticated: boolean
  user: SessionUser | null
}

export interface Connection {
  id: string
  label: string
  forge: string
  url: string
  identity: string
  last_status: string
  last_error_code: string | null
  last_checked_at: string | null
}

export const FORGE_DEFAULT_URL: Record<string, string> = {
  github: 'https://github.com',
  gitlab: 'https://gitlab.com',
  azure_devops: '',
}

export const api = {
  session: () => apiFetch<SessionState>('/api/session'),
  setup: (email: string, password: string) =>
    apiFetch<{ user: SessionUser }>('/api/setup', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  login: (email: string, password: string) =>
    apiFetch<{ user: SessionUser }>('/api/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  logout: () => apiFetch<{ ok: boolean }>('/api/logout', { method: 'POST' }),
  publicConnectors: () => apiFetch<{ id: string; label: string }[]>('/api/auth/connectors'),

  listSecrets: () =>
    apiFetch<{
      secrets: { slug: string; label: string; category: string; updated_at: string }[]
      categories: Record<string, string>
    }>('/api/secrets'),
  createSecret: (body: { slug: string; label: string; category: string; value: string }) =>
    apiFetch('/api/secrets', { method: 'POST', body: JSON.stringify(body) }),
  replaceSecretValue: (slug: string, value: string) =>
    apiFetch(`/api/secrets/${encodeURIComponent(slug)}/value`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  deleteSecret: (slug: string) =>
    apiFetch(`/api/secrets/${encodeURIComponent(slug)}`, { method: 'DELETE' }),

  listConnections: () =>
    apiFetch<{ connections: Connection[] }>('/api/connections'),
  createConnection: (body: { label: string; forge: string; url: string; token: string }) =>
    apiFetch<Connection>('/api/connections', { method: 'POST', body: JSON.stringify(body) }),
  testConnection: (id: string) =>
    apiFetch<Connection>(`/api/connections/${id}/test`, { method: 'POST' }),
  deleteConnection: (id: string) =>
    apiFetch(`/api/connections/${id}`, { method: 'DELETE' }),

  listConnectors: () =>
    apiFetch<{
      connectors: { id: string; label: string; issuer: string; accounts: number }[]
      default_scopes: string
    }>('/api/oidc/connectors'),
  createConnector: (body: {
    label: string
    issuer: string
    client_id: string
    client_secret: string
    scopes: string
  }) => apiFetch('/api/oidc/connectors', { method: 'POST', body: JSON.stringify(body) }),
  deleteConnector: (id: string) =>
    apiFetch(`/api/oidc/connectors/${id}`, { method: 'DELETE' }),
}
