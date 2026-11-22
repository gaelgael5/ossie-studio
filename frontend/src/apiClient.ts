import { ApiRequestError, type ApiErrorBody } from './errors'
import { withDraftView } from './draftView'

// La gouvernance vit dans le dépôt de chaque espace, par branche : les appels
// « /api/governance/… » visent l'espace et la branche choisis dans l'en-tête
// (mémorisés par le contexte d'espace de travail).
const GOVERNANCE = '/api/governance/'

// Espace et branche de CET onglet, posés par le contexte d'espace de travail.
// Pas le stockage local (partagé entre onglets) : un autre onglet qui change de
// branche ne doit pas faire écrire celui-ci dans la mauvaise branche.
let currentWorkspace: { space: string | null; branch: string | null } | null = null

export function setCurrentWorkspace(space: string | null, branch: string | null): void {
  currentWorkspace = { space, branch }
}

function storedWorkspace(): { space: string | null; branch: string | null } {
  if (currentWorkspace) return currentWorkspace
  try {
    return { space: localStorage.getItem('ossie.space'), branch: localStorage.getItem('ossie.branch') }
  } catch {
    return { space: null, branch: null }
  }
}

export function governancePath(path: string): string {
  if (!path.startsWith(GOVERNANCE)) return path
  const { space, branch } = storedWorkspace()
  if (!space || !branch) throw new ApiRequestError({ error: 'no_branch_selected' }, 400)
  return `/api/spaces/${space}/branches/${branch}/governance/${path.slice(GOVERNANCE.length)}`
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(withDraftView(governancePath(path), init?.method), {
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
  /** Capacités gagnées sur au moins un espace par une équipe rattachée. */
  space_capabilities?: string[]
  language?: string
}

/** Capacités qui ouvrent un écran : globales + gagnées sur au moins un espace.
 * Le serveur reste juge espace par espace ; l'écran ne fait que montrer. */
export function sessionCapabilities(session: SessionState | undefined): string[] {
  return [...(session?.capabilities ?? []), ...(session?.space_capabilities ?? [])]
}

export interface ProfileItem {
  id: string
  name: string
  slug: string
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
  teams: UserTeam[]
  space_rights: SpaceRight[]
}

export interface SpaceRight {
  space_id: string
  space_name: string
  profile_name: string
  team_name: string
}

export interface PersonalCredential {
  id: string
  connection_id: string
  forge: string
  url: string
  status: 'a_saisir' | 'jamais_teste' | 'valide' | 'refuse'
  forge_login: string | null
  error_code: string | null
  checked_at: string | null
}

export interface DraftObjectItem {
  path: string
  state: 'created' | 'modified' | 'deleted'
  changed_upstream: boolean
}

export interface MergeConflict {
  pointer: string
  base: unknown
  theirs: unknown
  mine: unknown
}

export interface MergeView {
  path: string
  state: 'created' | 'modified' | 'deleted'
  changed_upstream: boolean
  readonly: boolean
  kind: 'none' | 'auto' | 'conflict' | 'text' | 'text_conflict' | 'deleted_upstream' | 'deleted_in_draft'
  structured: boolean
  base: unknown
  theirs: unknown
  mine: unknown
  theirs_absent: boolean
  mine_absent: boolean
  proposed: unknown
  conflicts: MergeConflict[]
}

export type MergeChoice = 'theirs' | 'mine' | { value: unknown }

export interface ImpactItem {
  path: string
  reads: string[]
  rules: string[]
  verified: boolean
}

export interface ValidationEntry {
  level: 'error' | 'warning'
  path: string
  rule: string
  message_code: string
  args: Record<string, string>
  preexisting: boolean
  accepted: boolean
  blocking: boolean
}

export interface DraftSummary {
  count: number
  revision: number
  changed_upstream_count: number
  objects: DraftObjectItem[]
}

export interface SpaceTeam {
  team_id: string
  team_name: string
  profile_id: string
  profile_name: string
}

export interface UserTeam {
  id: string
  name: string
  origin: string
}

export interface TeamItem {
  id: string
  name: string
  slug: string
  origin: string
  profile_id?: string | null
  profile_name?: string | null
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
  slug: string
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
  slug: string
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

export interface DbConnectionItem {
  id: string
  label: string
  slug: string
  engine: string
  target: string
  identity: string
  server_version: string
  last_status: string
  last_error_code: string | null
  last_checked_at: string | null
  secret_slug: string
  secret_label: string
  secret_used_by_others: number
}

export interface DbEngineField {
  name: string
  kind: string
  default: string
  required: boolean
  options: string[]
}

export interface DbEngineSpec {
  engine: string
  secret_category: string
  secret_kind: string
  target_field: string
  fields: DbEngineField[]
}

export const FORGE_DEFAULT_URL: Record<string, string> = {
  github: 'https://github.com',
  gitlab: 'https://gitlab.com',
  azure_devops: '',
}

export interface GitSpace {
  id: string
  name: string
  slug: string
  connection_id: string
  repo_url: string
  last_probe_status: string | null
  last_probe_at: string | null
}

export interface SourceConnectionRef {
  slug: string
  label?: string
  kind: string
  forge?: string
  engine?: string
}
export interface SourceItem {
  slug: string
  ok: boolean
  label?: string
  type?: string
  connection?: SourceConnectionRef
  parameters?: Record<string, unknown>
  error?: string
}
export interface SourceInput {
  slug: string
  label: string
  type: string
  connection: string
  parameters: Record<string, unknown>
}

export interface CollectJobStatus {
  state: 'idle' | 'running' | 'done' | 'failed'
  started_at?: string
  finished_at?: string | null
  report?: CollectReport | null
  error?: { error: string; error_args: Record<string, unknown>; status: number } | null
  progress?: CollectProgressInfo
}

export interface CollectProgressInfo {
  step: string
  done: string[]
  scanned: number
  total: number
  processed: number
  current: string
}

export interface CollectReport {
  source_commit: string
  local_commit: string | null
  added: number
  modified: number
  deleted: number
  unchanged: number
  read: number
  retained: number
  duration_ms: number
  errors: { source: string; cause: string }[]
}
/** Un commit publié qui a touché la source (l'historique est porté par Git). */
export interface CollectionRun {
  sha: string
  author: string
  date: string
  message: string
}

export interface ContractAnalysis {
  openapi: string
  title: string
  declared_version: string
  schema_count: number
  operation_count: number
  external_refs: string[]
  proposed_version: string
}
export interface ContractItem {
  version: string
  file: string
  original_file: string
  openapi: string
  declared_version: string
  schema_count: number
  operation_count: number
  uploaded_at: string
  author: string
  collected: boolean
}
export interface ContractDeposit {
  filename: string
  content: string
  version: string
  replace: boolean
}

export interface PluginParamItem {
  slug: string
  label: string
  doc: string
  type: string
  default: string
  value: string
}
export interface ParsingPlugin {
  id: string
  label: string
  version: string
  cible: string
  globs: string[]
  parametres: PluginParamItem[]
}
export interface ScanFileItem {
  path: string
  crc: string
  cible: string
  statut: string
  message: string
  scanned_at: string
}

// --- Explorateur raw (lecture seule de la couche raw) ---
export type RawExplorerMode = 'origin' | 'kind' | 'parser'
export type RawBadge = 'retained' | 'new' | 'modified' | 'excluded' | 'error'

export interface RawCollectState {
  date: string
  files: number
  errors: number
}

export interface RawExplorerSource {
  slug: string
  label: string
  type: string
  collected: boolean
  objects: number
  documents: number
  last_collect: RawCollectState | null
}

export interface RawFolderItem {
  path: string
  /** Éléments directs : sous-dossiers + objets + documents. */
  items: number
  /** Objets du sous-arbre. */
  objects: number
}

export interface RawExplorerTree extends RawExplorerSource {
  mode: RawExplorerMode
  folders: RawFolderItem[]
}

export interface RawObjectItem {
  file: string
  title: string
  kind: string
  fields: number
  origin: string
  parser: string
  badges: RawBadge[]
  excluded_by: string
}

export interface RawDocumentItem {
  path: string
  title: string
  sections: number
}

export interface RawFolderContent {
  path: string
  folders: RawFolderItem[]
  objects: RawObjectItem[]
  documents: RawDocumentItem[]
}

export interface RawSearchResult extends RawObjectItem {
  folder: string
}

export interface CurationObjectItem {
  path: string
  title: string
  kind: string
  fields: number
  retained: 'none' | 'partial' | 'full'
  orphan: boolean
  excluded: boolean
  excluded_by: string
}

export interface TriRule {
  target: string
  pattern: string
  kind: string
  label?: string
}

export interface SourceRules {
  rules: TriRule[]
  targets: string[]
  kinds: string[]
}

export interface CurationField {
  name: string
  type: string
  retained: boolean
  ref: boolean
  missing: boolean
}

export interface CurationObjectDetail {
  path: string
  title: string
  fields: CurationField[]
}

export interface EnrichValues {
  business_name: string
  description: string
  concept: string
  tags: string[]
}

export interface EnrichScope extends EnrichValues {
  name: string
  readonly: Record<string, unknown>
}

export interface EnrichView {
  path: string
  title: string
  object: EnrichScope
  fields: EnrichScope[]
}

export interface ReviewFieldChange {
  name: string
  change: 'changed' | 'appeared' | 'disappeared'
  type: string
  lost_enrichment: string[]
}

export interface ReviewObjectItem {
  path: string
  title: string
  status: 'retained-touched' | 'object-disappeared' | 'new'
  fields: ReviewFieldChange[]
}

export interface ReviewResult {
  base_sha: string | null
  head_sha: string
  up_to_date: boolean
  objects: ReviewObjectItem[]
}

export interface ReviewFieldRef {
  path: string
  name: string
}

export interface ReviewApplyBody {
  head_sha: string
  apply_fields: ReviewFieldRef[]
  take_fields: ReviewFieldRef[]
  remove_fields: ReviewFieldRef[]
  remove_objects: string[]
  retain_objects: string[]
}

export interface ReviewHistoryCommit {
  sha: string
  author: string
  date: string
  message: string
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
  slug: string
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
  slug?: string
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

export type ApiKeyItem = {
  id: string
  label: string
  prefix: string
  agent_profile: { id: string; name: string }
  state: 'active' | 'revoked'
  created_at: string
  created_by: string | null
  revoked_at: string | null
  revoked_by: string | null
  last_used_at: string | null
}

export type ApiKeyOptions = {
  endpoint: string
  endpoint_configured: boolean
  agent_profiles: {
    id: string
    name: string
    description: string
    primitives: string[]
    grantable: boolean
    beyond: string[]
  }[]
}

export const api = {
  session: () => apiFetch<SessionState>('/api/session'),
  setLanguage: (language: string) =>
    apiFetch<{ ok: boolean }>('/api/session/language', {
      method: 'PUT',
      body: JSON.stringify({ language }),
    }),
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
  createSecret: (body: {
    slug: string
    label: string
    category: string
    value?: string
    key_file?: string
    passphrase?: string
  }) => apiFetch<{ slug: string }>('/api/secrets', { method: 'POST', body: JSON.stringify(body) }),
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
    apiFetch<{ repositories: { full_name: string; url: string; clone_url: string }[]; total: number }>(
      `/api/connections/${id}/repositories`,
    ),
  listDbConnections: () =>
    apiFetch<{ connections: DbConnectionItem[] }>('/api/db-connections'),
  listDbEngines: () =>
    apiFetch<{ engines: DbEngineSpec[] }>('/api/db-connections/engines'),
  createDbConnection: (body: {
    label: string
    slug: string
    engine: string
    params: Record<string, string>
    secret_slug: string
  }) => apiFetch<DbConnectionItem>('/api/db-connections', { method: 'POST', body: JSON.stringify(body) }),
  testDbConnection: (id: string) =>
    apiFetch<DbConnectionItem>(`/api/db-connections/${id}/test`, { method: 'POST' }),
  deleteDbConnection: (id: string, deleteSecret = false) =>
    apiFetch(`/api/db-connections/${id}?delete_secret=${deleteSecret}`, { method: 'DELETE' }),
  createConnection: (body: {
    label: string
    slug: string
    forge: string
    url: string
    secret_slug: string
  }) =>
    apiFetch<Connection>('/api/connections', { method: 'POST', body: JSON.stringify(body) }),
  updateConnection: (id: string, body: { label: string; url: string; secret_slug: string }) =>
    apiFetch<Connection>(`/api/connections/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  testConnection: (id: string) =>
    apiFetch<Connection>(`/api/connections/${id}/test`, { method: 'POST' }),
  deleteConnection: (id: string, deleteSecret = false) =>
    apiFetch(`/api/connections/${id}?delete_secret=${deleteSecret}`, { method: 'DELETE' }),

  listSpaces: () => apiFetch<{ spaces: GitSpace[] }>('/api/spaces'),
  mySpaceCapabilities: (spaceId: string) =>
    apiFetch<{ capabilities: string[] }>(`/api/spaces/${spaceId}/me`),
  myCredential: (spaceId: string) =>
    apiFetch<PersonalCredential>(`/api/me/credentials?space_id=${encodeURIComponent(spaceId)}`),
  setMyCredential: (id: string, value: string) =>
    apiFetch<PersonalCredential>(`/api/me/credentials/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  testMyCredential: (id: string, spaceId: string) =>
    apiFetch<PersonalCredential>(`/api/me/credentials/${id}/test`, {
      method: 'POST',
      body: JSON.stringify({ space_id: spaceId }),
    }),
  listSpaceTeams: (spaceId: string) => apiFetch<{ teams: SpaceTeam[] }>(`/api/spaces/${spaceId}/teams`),
  attachSpaceTeam: (spaceId: string, teamId: string, profileId: string) =>
    apiFetch<{ teams: SpaceTeam[] }>(`/api/spaces/${spaceId}/teams/${teamId}`, {
      method: 'PUT',
      body: JSON.stringify({ profile_id: profileId }),
    }),
  detachSpaceTeam: (spaceId: string, teamId: string) =>
    apiFetch(`/api/spaces/${spaceId}/teams/${teamId}`, { method: 'DELETE' }),
  listBranches: (spaceId: string) =>
    apiFetch<{
      branches: { id: string; label: string; slug: string; kind: string; git_ref: string }[]
      viewed_branch_id: string | null
    }>(`/api/spaces/${spaceId}/branches`),
  // Branche de base : adopter une branche existante (git_ref) OU en créer une (new_ref).
  createBaseBranch: (spaceId: string, body: { git_ref: string } | { new_ref: string }) =>
    apiFetch(`/api/spaces/${spaceId}/branches/base`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  listRemoteBranches: (spaceId: string) =>
    apiFetch<{ root: string; branches: { name: string; is_root: boolean; tracked: boolean }[] }>(
      `/api/spaces/${spaceId}/branches/remote`,
    ),
  // Branche fille : son nom git (pas de slug), tirée de la tête de la base.
  createChildBranch: (spaceId: string, newRef: string) =>
    apiFetch(`/api/spaces/${spaceId}/branches/child`, {
      method: 'POST',
      body: JSON.stringify({ new_ref: newRef }),
    }),
  setViewedBranch: (spaceId: string, branchId: string) =>
    apiFetch(`/api/spaces/${spaceId}/branches/viewed`, {
      method: 'PUT',
      body: JSON.stringify({ branch_id: branchId }),
    }),
  listConnectionBranches: (id: string, repo: string) =>
    apiFetch<{ branches: string[]; default: string }>(
      `/api/connections/${id}/branches?repo=${encodeURIComponent(repo)}`,
    ),
  listSources: (spaceId: string, branchId: string) =>
    apiFetch<{ sources: SourceItem[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources`,
    ),
  declareSource: (spaceId: string, branchId: string, body: SourceInput) =>
    apiFetch<SourceItem>(`/api/spaces/${spaceId}/branches/${branchId}/sources`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateSource: (
    spaceId: string,
    branchId: string,
    slug: string,
    body: Omit<SourceInput, 'slug' | 'type'>,
  ) =>
    apiFetch<SourceItem>(`/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  deleteSource: (spaceId: string, branchId: string, slug: string) =>
    apiFetch(`/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}`, {
      method: 'DELETE',
    }),
  // Rapport si la collecte finit vite ; sinon l'état « running » (202) à suivre
  // par getCollectStatus — la collecte continue côté serveur.
  collectSource: (spaceId: string, branchId: string, slug: string, force: boolean) =>
    apiFetch<CollectReport | CollectJobStatus>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/collect?force=${force}`,
      { method: 'POST' },
    ),
  getCollectStatus: (spaceId: string, branchId: string, slug: string) =>
    apiFetch<CollectJobStatus>(`/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/collect`),
  listCollections: (spaceId: string, branchId: string, slug: string) =>
    apiFetch<{ collections: CollectionRun[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/collections`,
    ),
  listContracts: (spaceId: string, branchId: string, slug: string) =>
    apiFetch<{ contracts: ContractItem[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/contracts`,
    ),
  analyzeContract: (
    spaceId: string,
    branchId: string,
    slug: string,
    filename: string,
    content: string,
  ) =>
    apiFetch<ContractAnalysis>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/contracts/analyze`,
      { method: 'POST', body: JSON.stringify({ filename, content }) },
    ),
  depositContract: (spaceId: string, branchId: string, slug: string, body: ContractDeposit) =>
    apiFetch<ContractAnalysis>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/contracts`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  removeContract: (spaceId: string, branchId: string, slug: string, version: string) =>
    apiFetch(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/contracts/${encodeURIComponent(version)}`,
      { method: 'DELETE' },
    ),
  contractDownloadUrl: (spaceId: string, branchId: string, slug: string, version: string) =>
    `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/contracts/${encodeURIComponent(version)}/download`,
  listSourceScan: (spaceId: string, branchId: string, slug: string) =>
    apiFetch<{ fichiers: ScanFileItem[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/scan`,
    ),
  listRawExplorerSources: (spaceId: string, branchId: string) =>
    apiFetch<{ sources: RawExplorerSource[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/raw-explorer`,
    ),
  getRawExplorerTree: (spaceId: string, branchId: string, slug: string, mode: RawExplorerMode) =>
    apiFetch<RawExplorerTree>(
      `/api/spaces/${spaceId}/branches/${branchId}/raw-explorer/${slug}/tree?mode=${mode}`,
    ),
  getRawExplorerFolder: (
    spaceId: string,
    branchId: string,
    slug: string,
    mode: RawExplorerMode,
    path: string,
  ) =>
    apiFetch<RawFolderContent>(
      `/api/spaces/${spaceId}/branches/${branchId}/raw-explorer/${slug}/folder?mode=${mode}&path=${encodeURIComponent(path)}`,
    ),
  searchRawExplorer: (
    spaceId: string,
    branchId: string,
    slug: string,
    mode: RawExplorerMode,
    q: string,
  ) =>
    apiFetch<{ results: RawSearchResult[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/raw-explorer/${slug}/search?mode=${mode}&q=${encodeURIComponent(q)}`,
    ),
  listCuration: (spaceId: string, branchId: string, slug: string, showExcluded = false) =>
    apiFetch<{ objects: CurationObjectItem[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation?show_excluded=${showExcluded}`,
    ),
  getCurationRules: (spaceId: string, branchId: string, slug: string) =>
    apiFetch<SourceRules>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/rules`,
    ),
  setCurationRules: (spaceId: string, branchId: string, slug: string, rules: TriRule[]) =>
    apiFetch<{ ok: boolean; commit: string | null; rules: TriRule[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/rules`,
      { method: 'PUT', body: JSON.stringify({ rules }) },
    ),
  getCurationObject: (spaceId: string, branchId: string, slug: string, path: string) =>
    apiFetch<CurationObjectDetail>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/object?path=${encodeURIComponent(path)}`,
    ),
  getEnrichment: (spaceId: string, branchId: string, slug: string, path: string) =>
    apiFetch<EnrichView>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/enrichment?path=${encodeURIComponent(path)}`,
    ),
  enrichAsset: (
    spaceId: string,
    branchId: string,
    slug: string,
    body: { path: string; obj: EnrichValues | null; fields: Record<string, EnrichValues> },
  ) =>
    apiFetch<{ ok: boolean; commit: string | null }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/enrich`,
      { method: 'PUT', body: JSON.stringify(body) },
    ),
  retainAsset: (
    spaceId: string,
    branchId: string,
    slug: string,
    path: string,
    fields: string[] | null,
  ) =>
    apiFetch<{ ok: boolean; commit: string | null }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/retain`,
      { method: 'POST', body: JSON.stringify({ path, fields }) },
    ),
  removeAsset: (spaceId: string, branchId: string, slug: string, path: string) =>
    apiFetch<{ ok: boolean; commit: string | null }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/remove`,
      { method: 'POST', body: JSON.stringify({ path }) },
    ),
  refreshAssets: (
    spaceId: string,
    branchId: string,
    slug: string,
    paths: string[] | null,
  ) =>
    apiFetch<{ ok: boolean; commit: string | null; refreshed: string[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/refresh`,
      { method: 'POST', body: JSON.stringify({ paths }) },
    ),
  getReview: (spaceId: string, branchId: string, slug: string) =>
    apiFetch<ReviewResult>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/review`,
    ),
  applyReview: (spaceId: string, branchId: string, slug: string, body: ReviewApplyBody) =>
    apiFetch<{ ok: boolean; commit: string | null }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/review/apply`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  getReviewHistory: (spaceId: string, branchId: string, slug: string, path: string) =>
    apiFetch<{ commits: ReviewHistoryCommit[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/sources/${slug}/curation/review/history?path=${encodeURIComponent(path)}`,
    ),
  listCodePlugins: () =>
    apiFetch<{ plugins: { id: string; label: string; globs: string[] }[] }>(
      '/api/parsing/code-plugins',
    ),
  listParsingPlugins: () => apiFetch<{ plugins: ParsingPlugin[] }>('/api/parsing/plugins'),
  setPluginParam: (pluginId: string, slug: string, value: string) =>
    apiFetch<{ plugin_id: string; slug: string; value: string }>(
      `/api/parsing/plugins/${encodeURIComponent(pluginId)}/parameters/${encodeURIComponent(slug)}`,
      { method: 'PUT', body: JSON.stringify({ value }) },
    ),
  draftMerge: (spaceId: string, branchId: string, path: string) =>
    apiFetch<MergeView>(
      `/api/spaces/${spaceId}/branches/${branchId}/draft/merge?path=${encodeURIComponent(path)}`,
    ),
  resolveMerge: (
    spaceId: string,
    branchId: string,
    body: { path: string; resolutions?: Record<string, MergeChoice>; delete?: boolean },
  ) =>
    apiFetch<{ path: string; merged: boolean }>(`/api/spaces/${spaceId}/branches/${branchId}/draft/merge`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  automerge: (spaceId: string, branchId: string) =>
    apiFetch<{ merged: string[]; conflicts: string[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/draft/automerge`,
      { method: 'POST' },
    ),
  draftImpacts: (spaceId: string, branchId: string) =>
    apiFetch<{ impacts: ImpactItem[]; pending: number }>(
      `/api/spaces/${spaceId}/branches/${branchId}/draft/impacts`,
    ),
  verifyImpact: (spaceId: string, branchId: string, path: string) =>
    apiFetch<{ path: string; verified: boolean }>(
      `/api/spaces/${spaceId}/branches/${branchId}/draft/impacts/verify`,
      { method: 'POST', body: JSON.stringify({ path }) },
    ),
  draftValidation: (spaceId: string, branchId: string) =>
    apiFetch<{ entries: ValidationEntry[]; blocking: number }>(
      `/api/spaces/${spaceId}/branches/${branchId}/draft/validation`,
    ),
  acceptWarning: (spaceId: string, branchId: string, entry: ValidationEntry) =>
    apiFetch<{ accepted: boolean }>(`/api/spaces/${spaceId}/branches/${branchId}/draft/validation/accept`, {
      method: 'POST',
      body: JSON.stringify({ rule: entry.rule, path: entry.path, message_code: entry.message_code }),
    }),
  publishReadiness: (spaceId: string, branchId: string) =>
    apiFetch<{ ok: boolean; reasons: string[]; conflicts: string[] }>(
      `/api/spaces/${spaceId}/branches/${branchId}/draft/readiness`,
    ),
  publishDraft: (spaceId: string, branchId: string, comment: string) =>
    apiFetch<{ commit: string; published: number }>(`/api/spaces/${spaceId}/branches/${branchId}/draft/publish`, {
      method: 'POST',
      body: JSON.stringify({ comment }),
    }),
  branchHead: (spaceId: string, branchId: string) =>
    apiFetch<{ sha: string }>(`/api/spaces/${spaceId}/branches/${branchId}/head`),
  myDraft: (spaceId: string, branchId: string) =>
    apiFetch<DraftSummary>(`/api/spaces/${spaceId}/branches/${branchId}/draft`),
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
  createSpace: (body: { name: string; slug: string; connection_id: string; repo_url: string }) =>
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
  createProfile: (body: { name: string; slug: string; description: string; capabilities: string[] }) =>
    apiFetch<ProfileItem>('/api/profiles', { method: 'POST', body: JSON.stringify(body) }),
  updateProfile: (
    id: string,
    body: { name: string; slug?: string; description: string; capabilities: string[] },
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
  createTeam: (name: string, slug = '') =>
    apiFetch<{ id: string; name: string; slug: string; origin: string }>('/api/teams', {
      method: 'POST',
      body: JSON.stringify({ name, slug }),
    }),
  renameTeam: (teamId: string, name: string) =>
    apiFetch<{ ok: boolean }>(`/api/teams/${teamId}`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    }),
  deleteTeam: (teamId: string) =>
    apiFetch<{ ok: boolean }>(`/api/teams/${teamId}`, { method: 'DELETE' }),
  setTeamProfile: (teamId: string, profileId: string | null) =>
    apiFetch<{ ok: boolean }>(`/api/teams/${teamId}/profile`, {
      method: 'PUT',
      body: JSON.stringify({ profile_id: profileId }),
    }),
  addUserTeam: (userId: string, teamId: string) =>
    apiFetch<{ ok: boolean }>(`/api/users/${userId}/teams`, {
      method: 'POST',
      body: JSON.stringify({ team_id: teamId }),
    }),
  removeUserTeam: (userId: string, teamId: string) =>
    apiFetch<{ ok: boolean; removed: boolean }>(`/api/users/${userId}/teams/${teamId}`, {
      method: 'DELETE',
    }),

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
    slug: string
    issuer: string
    client_id: string
    client_secret_slug: string
    scopes: string
  }) => apiFetch('/api/oidc/connectors', { method: 'POST', body: JSON.stringify(body) }),
  deleteConnector: (id: string, deleteSecret = false) =>
    apiFetch(`/api/oidc/connectors/${id}?delete_secret=${deleteSecret}`, { method: 'DELETE' }),
  listApiKeys: () => apiFetch<{ keys: ApiKeyItem[] }>('/api/api-keys'),
  apiKeyOptions: () => apiFetch<ApiKeyOptions>('/api/api-keys/options'),
  issueApiKey: (label: string, agentProfileId: string) =>
    apiFetch<{ key: ApiKeyItem; secret: string }>('/api/api-keys', {
      method: 'POST',
      body: JSON.stringify({ label, agent_profile_id: agentProfileId }),
    }),
  changeApiKeyProfile: (id: string, agentProfileId: string) =>
    apiFetch<ApiKeyItem>(`/api/api-keys/${id}/profile`, {
      method: 'PUT',
      body: JSON.stringify({ agent_profile_id: agentProfileId }),
    }),
  revokeApiKey: (id: string) =>
    apiFetch<ApiKeyItem>(`/api/api-keys/${id}/revoke`, { method: 'POST' }),
}
