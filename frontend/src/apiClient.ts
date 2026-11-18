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

export interface GitSpace {
  id: string
  name: string
  connection_id: string
  repo_url: string
  last_probe_status: string | null
  last_probe_at: string | null
}

export interface RailLine {
  ref: string
  name: string
  slug: string
  kind: string
  lane: number
  shape: string
}
export interface RailNode {
  sha: string
  short: string
  lane: number
  row: number
  kind: 'commit' | 'version' | 'unpublished'
  subject: string
  author: string
  date: string
  is_switch: boolean
  is_merge: boolean
  number: number | null
  label: string | null
  version_author: string | null
}
export interface RailEdge {
  child: string
  parent: string
  from_lane: number
  to_lane: number
  from_row: number
  to_row: number | null
  kind: 'normal' | 'switch' | 'merge'
}
export interface RailMapData {
  lines: RailLine[]
  nodes: RailNode[]
  edges: RailEdge[]
  total: number
  offset: number
  limit: number
  has_more: boolean
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

  listSpaces: () => apiFetch<{ spaces: GitSpace[] }>('/api/spaces'),
  listBranches: (spaceId: string) =>
    apiFetch<{
      branches: { id: string; label: string; slug: string; kind: string; git_ref: string }[]
      viewed_branch_id: string | null
    }>(`/api/spaces/${spaceId}/branches`),
  createBaseBranch: (spaceId: string, label: string) =>
    apiFetch(`/api/spaces/${spaceId}/branches/base`, {
      method: 'POST',
      body: JSON.stringify({ label }),
    }),
  createChildBranch: (spaceId: string, label: string) =>
    apiFetch(`/api/spaces/${spaceId}/branches/child`, {
      method: 'POST',
      body: JSON.stringify({ label }),
    }),
  setViewedBranch: (spaceId: string, branchId: string) =>
    apiFetch(`/api/spaces/${spaceId}/branches/viewed`, {
      method: 'PUT',
      body: JSON.stringify({ branch_id: branchId }),
    }),
  listUnpublished: (spaceId: string, branchId: string) =>
    apiFetch<{
      commits: { sha: string; author: string; date: string; message: string }[]
      count: number
    }>(`/api/spaces/${spaceId}/branches/${branchId}/unpublished`),
  publishBranch: (spaceId: string, branchId: string) =>
    apiFetch<{ published: number }>(`/api/spaces/${spaceId}/branches/${branchId}/publish`, {
      method: 'POST',
    }),
  listVersions: (spaceId: string, branchId: string) =>
    apiFetch<{
      versions: { number: number; label: string; commit: string; author: string; date: string }[]
    }>(`/api/spaces/${spaceId}/branches/${branchId}/versions`),
  createVersion: (spaceId: string, branchId: string, label: string) =>
    apiFetch<{ number: number; commit: string; already_exists: boolean }>(
      `/api/spaces/${spaceId}/branches/${branchId}/versions`,
      { method: 'POST', body: JSON.stringify({ label }) },
    ),
  restoreVersion: (spaceId: string, branchId: string, number: number) =>
    apiFetch<{ number: number; label: string; already_current: boolean; created: boolean }>(
      `/api/spaces/${spaceId}/branches/${branchId}/versions/${number}/restore`,
      { method: 'POST' },
    ),
  getRailmap: (spaceId: string, limit: number) =>
    apiFetch<RailMapData>(`/api/spaces/${spaceId}/railmap?limit=${limit}`),
  createSpace: (body: { name: string; connection_id: string; repo_url: string }) =>
    apiFetch<GitSpace & { probe: unknown }>('/api/spaces', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  probeSpace: (id: string) =>
    apiFetch<{ ok: boolean }>(`/api/spaces/${id}/probe`, { method: 'POST' }),
  deleteSpace: (id: string) => apiFetch(`/api/spaces/${id}`, { method: 'DELETE' }),

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
