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
  capabilities?: string[]
}

export interface ProfileItem {
  id: string
  name: string
  description: string
  capabilities: string[]
  is_admin: boolean
  is_default: boolean
  user_count: number
}
export interface CapabilityDomain {
  domain: string
  capabilities: string[]
}

export interface UserItem {
  id: string
  email: string
  display_name: string
  is_oidc: boolean
  last_login_at: string | null
  profile_id: string | null
  profile_name: string | null
  profile_pinned: boolean
  team_id: string | null
  team_name: string | null
  team_origin: string | null
  team_pinned: boolean
}

export interface TeamItem {
  id: string
  name: string
  slug: string
  origin: string
}

export interface RoleMapping {
  id: string
  role_value: string
  profile_id: string
  profile_name: string | null
}
export interface ConnectorTeam {
  id: string
  name: string
  slug: string
}
export interface OidcConnectorItem {
  id: string
  label: string
  issuer: string
  accounts: number
  client_secret_slug: string
  roles_claim: string
  mappings: RoleMapping[]
  team_claim: string
  teams: ConnectorTeam[]
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
  secret_slug: string
  secret_label: string
  secret_used_by_others: number
  used_by_spaces: number
  used_by_sources: number
}

export interface ForgeSpec {
  forge: string
  default_url: string
  url_required: boolean
  secret_category: string
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

export interface GovernedTag {
  id: string
  owner: string
  description: string
  links: Record<string, string>
  properties: Record<string, string | number | boolean>
  created_at: string
  updated_at: string
}
export interface GovernedTagInput {
  id?: string
  owner: string
  description: string
  links: Record<string, string>
  properties: Record<string, string | number | boolean>
}

export interface Certification {
  id: string
  name: string
  rank: number
  description: string
  criteria: string
  tag: string
  icon: string
  restricted: boolean
  created_at: string
  updated_at: string
}
export interface CertificationInput {
  name: string
  rank: number
  description: string
  criteria: string
  tag: string
  icon: string
  restricted: boolean
}

export interface ClassificationLevel {
  id: string
  name: string
  label: string
  short_name: string
  rank: number
  description: string
  criteria: string
  color: string
  icon: string
  position: number
}
export interface ClassificationScheme {
  id: string
  name: string
  description: string
  badge_prefix: string
  multi_value: boolean
  is_primary: boolean
  display_order: number
  levels: ClassificationLevel[]
}
export interface SchemeInput {
  id?: string
  name: string
  description: string
  badge_prefix: string
  multi_value: boolean
  is_primary: boolean
  levels?: LevelInput[]
}
export interface LevelInput {
  name: string
  label: string
  short_name: string
  rank: number
  description?: string
  criteria?: string
  color?: string
  icon?: string
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

  listSecrets: (category?: string) =>
    apiFetch<{
      secrets: {
        slug: string
        label: string
        category: string
        updated_at: string
        used_by: number
      }[]
      categories: Record<string, string>
      category_inputs: Record<string, string>
    }>(`/api/secrets${category ? `?category=${encodeURIComponent(category)}` : ''}`),
  createSecret: (body: { slug: string; label: string; category: string; value: string }) =>
    apiFetch('/api/secrets', { method: 'POST', body: JSON.stringify(body) }),
  replaceSecretValue: (slug: string, value: string) =>
    apiFetch(`/api/secrets/${encodeURIComponent(slug)}/value`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  updateSecretLabel: (slug: string, label: string) =>
    apiFetch(`/api/secrets/${encodeURIComponent(slug)}/label`, {
      method: 'PUT',
      body: JSON.stringify({ label }),
    }),
  deleteSecret: (slug: string) =>
    apiFetch(`/api/secrets/${encodeURIComponent(slug)}`, { method: 'DELETE' }),

  listConnections: () =>
    apiFetch<{ connections: Connection[] }>('/api/connections'),
  listForges: () =>
    apiFetch<{ forges: ForgeSpec[] }>('/api/connections/forges'),
  listConnectionRepositories: (id: string) =>
    apiFetch<{ repositories: { full_name: string; url: string }[]; total: number }>(
      `/api/connections/${id}/repositories`,
    ),
  createConnection: (body: { label: string; forge: string; url: string; secret_slug: string }) =>
    apiFetch<Connection>('/api/connections', { method: 'POST', body: JSON.stringify(body) }),
  testConnection: (id: string) =>
    apiFetch<Connection>(`/api/connections/${id}/test`, { method: 'POST' }),
  deleteConnection: (id: string, deleteSecret = false) =>
    apiFetch(`/api/connections/${id}?delete_secret=${deleteSecret}`, { method: 'DELETE' }),

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

  listGovernedTags: () => apiFetch<{ tags: GovernedTag[] }>('/api/governance/tags'),
  createGovernedTag: (body: GovernedTagInput) =>
    apiFetch<GovernedTag>('/api/governance/tags', { method: 'POST', body: JSON.stringify(body) }),
  updateGovernedTag: (id: string, body: GovernedTagInput) =>
    apiFetch<GovernedTag>(`/api/governance/tags/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteGovernedTag: (id: string) =>
    apiFetch(`/api/governance/tags/${id}`, { method: 'DELETE' }),
  exportGovernedTags: () => apiFetch<{ yaml: string }>('/api/governance/tags/export'),
  importGovernedTags: (yaml: string) =>
    apiFetch<{ imported: number }>('/api/governance/tags/import', {
      method: 'POST',
      body: JSON.stringify({ yaml }),
    }),

  listCertifications: () =>
    apiFetch<{ certifications: Certification[] }>('/api/governance/certifications'),
  createCertification: (body: CertificationInput) =>
    apiFetch<Certification>('/api/governance/certifications', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateCertification: (id: string, body: CertificationInput) =>
    apiFetch<Certification>(`/api/governance/certifications/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteCertification: (id: string) =>
    apiFetch(`/api/governance/certifications/${id}`, { method: 'DELETE' }),
  createCertificationPresets: () =>
    apiFetch<{ certifications: Certification[] }>('/api/governance/certifications/presets', {
      method: 'POST',
    }),

  listSchemes: () =>
    apiFetch<{ schemes: ClassificationScheme[] }>('/api/governance/classifications'),
  createScheme: (body: SchemeInput) =>
    apiFetch<ClassificationScheme>('/api/governance/classifications', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateScheme: (id: string, body: Omit<SchemeInput, 'id' | 'levels'> & { display_order: number }) =>
    apiFetch<ClassificationScheme>(`/api/governance/classifications/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteScheme: (id: string) =>
    apiFetch(`/api/governance/classifications/${id}`, { method: 'DELETE' }),
  addLevel: (schemeId: string, body: LevelInput) =>
    apiFetch<ClassificationLevel>(`/api/governance/classifications/${schemeId}/levels`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateLevel: (schemeId: string, levelId: string, body: LevelInput) =>
    apiFetch<ClassificationLevel>(
      `/api/governance/classifications/${schemeId}/levels/${levelId}`,
      { method: 'PUT', body: JSON.stringify(body) },
    ),
  deleteLevel: (schemeId: string, levelId: string) =>
    apiFetch(`/api/governance/classifications/${schemeId}/levels/${levelId}`, { method: 'DELETE' }),
  reorderLevels: (schemeId: string, order: string[]) =>
    apiFetch(`/api/governance/classifications/${schemeId}/levels`, {
      method: 'PUT',
      body: JSON.stringify({ order }),
    }),
  exportScheme: (id: string) =>
    apiFetch<{ yaml: string }>(`/api/governance/classifications/${id}/export`),
  importScheme: (yaml: string) =>
    apiFetch<ClassificationScheme>('/api/governance/classifications/import', {
      method: 'POST',
      body: JSON.stringify({ yaml }),
    }),

  listProfiles: () =>
    apiFetch<{ profiles: ProfileItem[]; catalog: CapabilityDomain[] }>('/api/profiles'),
  createProfile: (body: { name: string; description: string; capabilities: string[] }) =>
    apiFetch<ProfileItem>('/api/profiles', { method: 'POST', body: JSON.stringify(body) }),
  updateProfile: (
    id: string,
    body: { name: string; description: string; capabilities: string[] },
  ) =>
    apiFetch<ProfileItem>(`/api/profiles/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  duplicateProfile: (id: string) =>
    apiFetch<ProfileItem>(`/api/profiles/${id}/duplicate`, { method: 'POST' }),
  setDefaultProfile: (id: string) =>
    apiFetch<ProfileItem>(`/api/profiles/${id}/default`, { method: 'POST' }),
  deleteProfile: (id: string) => apiFetch(`/api/profiles/${id}`, { method: 'DELETE' }),

  searchUsers: (q: string, profileId: string, teamId: string, limit: number, offset: number) => {
    const p = new URLSearchParams({
      q,
      profile_id: profileId,
      team_id: teamId,
      limit: String(limit),
      offset: String(offset),
    })
    return apiFetch<{ users: UserItem[]; total: number }>(`/api/users?${p.toString()}`)
  },
  assignUserProfile: (userId: string, profileId: string) =>
    apiFetch<{ ok: boolean }>(`/api/users/${userId}/profile`, {
      method: 'PUT',
      body: JSON.stringify({ profile_id: profileId }),
    }),
  listTeams: () => apiFetch<{ teams: TeamItem[] }>('/api/teams'),
  createTeam: (name: string) =>
    apiFetch<{ id: string; name: string; origin: string }>('/api/teams', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  assignUserTeam: (userId: string, teamId: string) =>
    apiFetch<{ ok: boolean }>(`/api/users/${userId}/team`, {
      method: 'PUT',
      body: JSON.stringify({ team_id: teamId }),
    }),
  removeUserTeamPin: (userId: string) =>
    apiFetch(`/api/users/${userId}/team-pin`, { method: 'DELETE' }),

  listConnectors: () =>
    apiFetch<{
      connectors: OidcConnectorItem[]
      default_scopes: string
      pinned_count: number
      callback_url: string
    }>('/api/oidc/connectors'),
  connectorsDiscovery: () =>
    apiFetch<Record<string, string>>('/api/oidc/connectors/discovery'),
  checkDiscovery: (issuer: string) =>
    apiFetch<{ ok: boolean }>('/api/oidc/connectors/check-discovery', {
      method: 'POST',
      body: JSON.stringify({ issuer }),
    }),
  setRolesClaim: (connectorId: string, rolesClaim: string) =>
    apiFetch(`/api/oidc/connectors/${connectorId}/roles-claim`, {
      method: 'PUT',
      body: JSON.stringify({ roles_claim: rolesClaim }),
    }),
  setTeamClaim: (connectorId: string, teamClaim: string) =>
    apiFetch(`/api/oidc/connectors/${connectorId}/team-claim`, {
      method: 'PUT',
      body: JSON.stringify({ team_claim: teamClaim }),
    }),
  addRoleMapping: (connectorId: string, roleValue: string, profileId: string) =>
    apiFetch(`/api/oidc/connectors/${connectorId}/mappings`, {
      method: 'POST',
      body: JSON.stringify({ role_value: roleValue, profile_id: profileId }),
    }),
  deleteRoleMapping: (connectorId: string, mappingId: string) =>
    apiFetch(`/api/oidc/connectors/${connectorId}/mappings/${mappingId}`, { method: 'DELETE' }),
  reorderRoleMappings: (connectorId: string, order: string[]) =>
    apiFetch(`/api/oidc/connectors/${connectorId}/mappings`, {
      method: 'PUT',
      body: JSON.stringify({ order }),
    }),
  removeUserPin: (userId: string) =>
    apiFetch(`/api/users/${userId}/pin`, { method: 'DELETE' }),
  createConnector: (body: {
    label: string
    issuer: string
    client_id: string
    client_secret_slug: string
    scopes: string
  }) => apiFetch('/api/oidc/connectors', { method: 'POST', body: JSON.stringify(body) }),
  deleteConnector: (id: string, deleteSecret = false) =>
    apiFetch(`/api/oidc/connectors/${id}?delete_secret=${deleteSecret}`, { method: 'DELETE' }),
}
