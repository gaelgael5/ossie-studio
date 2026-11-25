import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import type { SourceInput, SourceItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { NoBranchNote } from '../components/NoBranchNote'
import { Combobox } from '../components/Combobox'
import { useWorkspace } from '../workspace'
import { RawExplorer } from './RawExplorer'
import { exclusionsOf, parseExclusionInput } from '../sourceExclusions'
import { SchemaPicker } from '../components/SchemaPicker'
import { TriRulesEditor, useTriRules } from '../components/TriRulesEditor'
import { ClassBadges } from '../components/ClassBadges'

// Types que le serveur sait collecter (sources/manifest.py) : les bases en
// étaient absentes, leur ligne n'avait pas de lien « Collecter » (0d85e746).
const COLLECTABLE_TYPES = ['git', 'openapi', 'postgres', 'sqlserver', 'snowflake']

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

function whatIsRead(source: SourceItem, t: (k: string, o?: Record<string, unknown>) => string): string {
  const read = whatIsReadBase(source, t)
  const rules = source.triage_count ?? 0
  return rules > 0 ? `${read} · ${t('triageCountShort', { count: rules })}` : read
}

function whatIsReadBase(source: SourceItem, t: (k: string) => string): string {
  const p = source.parameters ?? {}
  if (source.type === 'openapi') {
    return t('openapiContractsRead')
  }
  if (source.type === 'git') {
    const dirs = asList(p.directories)
    const where = dirs.length > 0 ? dirs.join(', ') : t('wholeRepo')
    const excluded = exclusionsOf(p)
    const suffix = excluded.length > 0 ? ` · ${t('excludedDirsShort')} ${excluded.join(', ')}` : ''
    return `${String(p.repository ?? '')} @ ${String(p.branch ?? '')} · ${where}${suffix}`
  }
  const schemas = asList(p.schemas)
  return schemas.length > 0 ? `${String(p.database ?? '')} · ${schemas.join(', ')}` : String(p.database ?? '')
}

type Editing =
  | { kind: 'git'; source?: SourceItem }
  | { kind: 'sql'; source?: SourceItem }
  | { kind: 'openapi'; source?: SourceItem }
  | null

export function SourcesPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [menuOpen, setMenuOpen] = useState(false)
  const [editing, setEditing] = useState<Editing>(null)
  const [messages, setMessages] = useState<string[]>([])
  // L'onglet vit dans l'adresse (?tab=raw) : un lien vers l'explorateur, ouvert
  // sur une source et un dossier, se partage et se rouvre tel quel.
  const [searchParams, setSearchParams] = useSearchParams()
  const rawTab = searchParams.get('tab') === 'raw'

  const key = ['sources', ws.spaceId, ws.branchId]
  const sources = useQuery({
    queryKey: key,
    queryFn: () => api.listSources(ws.spaceId as string, ws.branchId as string),
    enabled: !!ws.spaceId && !!ws.branchId,
    retry: false,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['sources'] })
  // « Déclarer et collecter » (popups Git et SQL) : la collecte part en tâche de
  // fond côté serveur ; la page de la source suit son état à l'ouverture.
  const declaredCollect = async (slug: string) => {
    setEditing(null)
    await invalidate()
    void api
      .collectSource(ws.spaceId as string, ws.branchId as string, slug, false)
      .catch(() => undefined)
      .finally(() => void queryClient.invalidateQueries({ queryKey: ['collect-status'] }))
    await queryClient.invalidateQueries({ queryKey: ['collect-status'] })
    navigate(`/sources/${slug}`)
  }
  // Schémas de classification, pour colorer le niveau de chaque source (ticket
  // 5e8680d7). Sans droit de lecture de la gouvernance, le niveau s'affiche en texte.
  const hasClassification = (sources.data?.sources ?? []).some((s) => (s.classification ?? []).length > 0)
  const schemes = useQuery({
    queryKey: ['schemes'],
    queryFn: api.listSchemes,
    enabled: hasClassification,
    retry: false,
  })

  const remove = useMutation({
    mutationFn: (slug: string) =>
      api.deleteSource(ws.spaceId as string, ws.branchId as string, slug),
    onSuccess: invalidate,
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  if (ws.spaces.length === 0) {
    return (
      <Frame>
        <div className="empty-note">{t('noSpaceYet')}</div>
      </Frame>
    )
  }
  if (!ws.branchId) {
    return (
      <Frame>
        <NoBranchNote spaceId={ws.spaceId} />
      </Frame>
    )
  }

  const list = sources.data?.sources ?? []

  return (
    <Frame>
      <div className="page-head">
        <div>
          <div className="breadcrumb">
            {ws.spaces.find((s) => s.id === ws.spaceId)?.name} ›{' '}
            {ws.branches.find((b) => b.id === ws.branchId)?.label}
          </div>
          <h1 className="page-title">{t('navSources')}</h1>
          <p className="page-sub">{t('sourcesSubtitle')}</p>
        </div>
        <div className="page-actions">
          <div className="topnav-dropdown">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup="true"
              aria-expanded={menuOpen}
            >
              {t('sourcesDeclare')}
              {' ▾'}
            </button>
            {menuOpen ? (
              <div className="topnav-dropdown-menu" role="menu" style={{ left: 'auto', right: 0, minWidth: 260 }}>
                <button
                  type="button"
                  className="btn btn-sm"
                  style={{ margin: 4, textAlign: 'left' }}
                  onClick={() => {
                    setMenuOpen(false)
                    setEditing({ kind: 'git' })
                  }}
                >
                  {t('sourceTypeGit')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  style={{ margin: 4, textAlign: 'left' }}
                  onClick={() => {
                    setMenuOpen(false)
                    setEditing({ kind: 'sql' })
                  }}
                >
                  {t('sourceTypeSql')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  style={{ margin: 4, textAlign: 'left' }}
                  onClick={() => {
                    setMenuOpen(false)
                    setEditing({ kind: 'openapi' })
                  }}
                >
                  {t('sourceTypeOpenapi')}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={!rawTab}
          className={`tab${rawTab ? '' : ' active'}`}
          onClick={() => setSearchParams({})}
        >
          {t('tabSourcesDeclared')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={rawTab}
          className={`tab${rawTab ? ' active' : ''}`}
          onClick={() => setSearchParams({ tab: 'raw' })}
        >
          {t('tabRawExplorer')}
        </button>
      </div>

      <ErrorList messages={messages} />

      {rawTab ? (
        <RawExplorer
          spaceId={ws.spaceId as string}
          branchId={ws.branchId as string}
          spaceName={ws.spaces.find((s) => s.id === ws.spaceId)?.name ?? ''}
        />
      ) : (
        <>
          <div className="panel">
            {list.length > 0 ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('colSource')}</th>
                    <th>{t('colTypeConnexion')}</th>
                    <th>{t('colWhatRead')}</th>
                    <th>{t('colState')}</th>
                    <th aria-label={t('colActions')} />
                  </tr>
                </thead>
                <tbody>
                  {list.map((source) => (
                    // Toute la ligne ouvre la source (demande de Gael du 24/11) ; les
                    // liens et boutons de la ligne gardent leur propre action.
                    <tr
                      key={source.slug}
                      className={source.ok ? 'row-clickable' : undefined}
                      onClick={(e) => {
                        if (!source.ok || (e.target as HTMLElement).closest('a, button, input, select')) return
                        navigate(`/sources/${source.slug}`)
                      }}
                    >
                      <td>
                        {source.ok ? (
                          <>
                            <Link to={`/sources/${source.slug}`} className="link-strong">
                              {source.label}
                            </Link>
                            <div className="muted mono small">
                              {source.slug}
                            </div>
                            {(source.classification ?? []).map((c) => {
                              const scheme = schemes.data?.schemes.find((sc) => sc.id === c.scheme)
                              return scheme ? (
                                <ClassBadges key={c.scheme} scheme={scheme} values={c.values} declared={false} />
                              ) : (
                                <span key={c.scheme} className="pill">
                                  {c.values.join(', ')}
                                </span>
                              )
                            })}
                          </>
                        ) : (
                          <span className="mono">{source.slug}</span>
                        )}
                      </td>
                      <td>
                        {source.ok ? (
                          <>
                            {source.type}
                            <div className="muted small">
                              {source.connection?.kind === 'missing'
                                ? t('connectionMissing')
                                : source.connection?.kind === 'none'
                                  ? t('sourceNoConnection')
                                  : source.connection?.label}
                            </div>
                          </>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className="muted">{source.ok ? whatIsRead(source, t) : ''}</td>
                      <td>
                        {source.ok ? (
                          <span className="pill text-ok">
                            {t('sourceValid')}
                          </span>
                        ) : (
                          <span className="text-danger">
                            {t(`fields.${source.error}`, {
                              defaultValue: t('sourceInError'),
                            })}
                          </span>
                        )}
                      </td>
                      <td>
                        <div className="row">
                          {source.ok && COLLECTABLE_TYPES.includes(source.type ?? '') ? (
                            <Link to={`/sources/${source.slug}`} className="btn btn-sm btn-primary">
                              {source.type === 'openapi' ? t('openContracts') : t('collect')}
                            </Link>
                          ) : null}
                          {source.ok ? (
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() =>
                                setEditing({
                                  kind:
                                    source.type === 'git'
                                      ? 'git'
                                      : source.type === 'openapi'
                                        ? 'openapi'
                                        : 'sql',
                                  source,
                                })
                              }
                            >
                              {t('editSource')}
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            onClick={() => {
                              if (window.confirm(t('deleteSourceConfirm'))) remove.mutate(source.slug)
                            }}
                          >
                            {t('delete')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="empty-note">{t('noSources')}</div>
            )}
          </div>

          <div className="info-note" style={{ marginTop: 16 }}>
            {t('sourceSecretNote')}
          </div>
        </>
      )}

      {editing?.kind === 'git' ? (
        <GitSourceModal
          spaceId={ws.spaceId as string}
          branchId={ws.branchId as string}
          source={editing.source}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null)
            await invalidate()
          }}
          onDeclaredCollect={declaredCollect}
        />
      ) : null}
      {editing?.kind === 'sql' ? (
        <SqlSourceModal
          spaceId={ws.spaceId as string}
          branchId={ws.branchId as string}
          source={editing.source}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null)
            await invalidate()
          }}
          onDeclaredCollect={declaredCollect}
        />
      ) : null}
      {editing?.kind === 'openapi' ? (
        <OpenApiSourceModal
          spaceId={ws.spaceId as string}
          branchId={ws.branchId as string}
          source={editing.source}
          onClose={() => setEditing(null)}
          onSaved={async (slug) => {
            setEditing(null)
            await invalidate()
            if (slug) navigate(`/sources/${slug}`)
          }}
        />
      ) : null}
    </Frame>
  )
}

function OpenApiSourceModal({
  spaceId,
  branchId,
  source,
  onClose,
  onSaved,
}: {
  spaceId: string
  branchId: string
  source?: SourceItem
  onClose: () => void
  onSaved: (slug?: string) => void
}) {
  const { t } = useTranslation()
  const editMode = source !== undefined
  // Paire libellé/slug : règle commune (components/LabelSlugFields).
  const pair = useLabelSlug({ label: source?.label, slug: source?.slug })
  const { label } = pair
  const slug = pair.finalSlug
  const [messages, setMessages] = useState<string[]>([])
  const tri = useTriRules(spaceId, branchId, source?.slug)


  const save = useMutation({
    mutationFn: () => {
      const body: SourceInput = {
        slug,
        label,
        type: 'openapi',
        connection: '',
        parameters: {},
      }
      return editMode
        ? api
            .updateSource(spaceId, branchId, source!.slug, {
              label,
              connection: '',
              parameters: {},
            })
            .then(async (saved) => {
              await tri.persist()
              return saved
            })
        : api.declareSource(spaceId, branchId, body)
    },
    // À la création, on ouvre le détail pour y déposer les contrats.
    onSuccess: (saved) => onSaved(editMode ? undefined : saved.slug),
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  return (
    <Modal
      title={editMode ? t('editOpenapiSource') : t('newOpenapiSource')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={save.isPending || !label || !slug}
            onClick={() => {
              setMessages([])
              save.mutate()
            }}
          >
            {editMode ? t('declare') : t('declareOpenapi')}
          </button>
        </>
      }
    >
      <ErrorList messages={messages} />
      <div className="stack">
        <p className="muted">{t('openapiSourceIntro')}</p>
        <div className="form-grid">
          <LabelSlugFields
            idPrefix="o"
            state={pair}
            labelText={t('fieldLabel')}
            slugText={t('fieldSlug')}
            slugLocked={editMode}
          />
        </div>
        {editMode ? <TriRulesEditor state={tri} /> : null}
        <div className="info-note">{t('openapiSourceNote')}</div>
      </div>
    </Modal>
  )
}

function Frame({ children }: { children: ReactNode }) {
  return <main>{children}</main>
}

function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer: ReactNode
}) {
  return (
    <div
      role="dialog"
      aria-label={title}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15,27,45,0.5)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        padding: 24,
        zIndex: 50,
        overflow: 'auto',
      }}
    >
      <div className="panel" style={{ width: 'min(960px, 100%)', marginTop: 24 }}>
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label="close">
            {'✕'}
          </button>
        </div>
        {children}
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
          {footer}
        </div>
      </div>
    </div>
  )
}

function ManifestPreview({ manifest }: { manifest: Record<string, unknown> }) {
  const { t } = useTranslation()
  const lines = Object.entries(manifest).map(([k, v]) => {
    if (v && typeof v === 'object') {
      const inner = Object.entries(v as Record<string, unknown>)
        .map(([ik, iv]) => `  ${ik}: ${Array.isArray(iv) ? `[${iv.join(', ')}]` : String(iv)}`)
        .join('\n')
      return `${k}:\n${inner}`
    }
    return `${k}: ${String(v)}`
  })
  return (
    <div>
      <p className="panel-label">{t('manifestPreview')}</p>
      <pre
        className="mono"
        style={{
          background: 'var(--nav-bg)',
          color: 'var(--nav-chip-fg)',
          padding: 14,
          borderRadius: 'var(--radius)',
          overflow: 'auto',
          // Revient à la ligne : une longue liste de parseurs ne doit pas
          // élargir la colonne au-delà de la fenêtre.
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          fontSize: '0.82rem',
        }}
      >
        {`version: 2\n${lines.join('\n')}`}
      </pre>
    </div>
  )
}

function GitSourceModal({
  spaceId,
  branchId,
  source,
  onClose,
  onSaved,
  onDeclaredCollect,
}: {
  spaceId: string
  branchId: string
  source?: SourceItem
  onClose: () => void
  onSaved: () => void
  onDeclaredCollect: (slug: string) => void
}) {
  const { t } = useTranslation()
  const editMode = source !== undefined
  const p = source?.parameters ?? {}
  // Paire libellé/slug : règle commune (components/LabelSlugFields).
  const pair = useLabelSlug({ label: source?.label, slug: source?.slug })
  const { label } = pair
  const slug = pair.finalSlug
  // On garde l'id de la connexion dans l'état (pour lister dépôts et branches) ;
  // le manifeste, lui, reçoit le slug.
  const [connectionId, setConnectionId] = useState('')
  const [repository, setRepository] = useState(String(p.repository ?? ''))
  const [branchChoice, setBranch] = useState(String(p.branch ?? ''))
  const [dirs, setDirs] = useState(asList(p.directories).join(', '))
  const [excludedDirs, setExcludedDirs] = useState(exclusionsOf(p).join(', '))
  const [messages, setMessages] = useState<string[]>([])
  const tri = useTriRules(spaceId, branchId, source?.slug)

  const connections = useQuery({ queryKey: ['connections'], queryFn: api.listConnections })
  const repos = useQuery({
    queryKey: ['repos', connectionId],
    queryFn: () => api.listConnectionRepositories(connectionId),
    enabled: !!connectionId,
    retry: false,
  })
  const branches = useQuery({
    queryKey: ['forge-branches', connectionId, repository],
    queryFn: () => api.listConnectionBranches(connectionId, repository),
    enabled: !!connectionId && !!repository,
    retry: false,
  })
  // Sans choix explicite, la branche par défaut du dépôt (déclarée par la forge,
  // sinon main / master / root) : dérivée au rendu, jamais recopiée en état.
  const branch = branchChoice || (branches.data?.default ?? '')

  const forges = useMemo(() => connections.data?.connections ?? [], [connections.data])

  const codePlugins = useQuery({ queryKey: ['code-plugins'], queryFn: api.listCodePlugins })
  const plugins = useMemo(() => codePlugins.data?.plugins ?? [], [codePlugins.data])
  const [parsers, setParsers] = useState<string[]>(asList(p.parsers))
  const parsersTouched = useRef(editMode)
  // Nouvelle source : on pré-coche tous les parseurs de code dès qu'ils sont connus.
  useEffect(() => {
    if (!parsersTouched.current && plugins.length > 0) {
      setParsers(plugins.map((pl) => pl.id))
    }
  }, [plugins])


  // Édition : retrouver l'id de la connexion à partir du slug mémorisé.
  useEffect(() => {
    if (editMode && !connectionId && source?.connection?.slug) {
      const match = forges.find((c) => c.slug === source.connection?.slug)
      if (match) setConnectionId(match.id)
    }
  }, [editMode, connectionId, forges, source])

  const parameters: Record<string, unknown> = {
    repository,
    branch,
    parsers,
    ...(dirs.trim()
      ? { directories: dirs.split(',').map((d) => d.trim()).filter(Boolean) }
      : {}),
    ...(parseExclusionInput(excludedDirs).length > 0
      ? { exclusions: parseExclusionInput(excludedDirs) }
      : {}),
  }
  const connSlug = forges.find((c) => c.id === connectionId)?.slug ?? ''
  const preview = { slug, label, type: 'git', connection: connSlug, parameters }

  const collectAfter = useRef(false)
  const save = useMutation({
    mutationFn: () => {
      const body: SourceInput = { slug, label, type: 'git', connection: connSlug, parameters }
      return editMode
        ? api
            .updateSource(spaceId, branchId, source!.slug, {
              label,
              connection: connSlug,
              parameters,
            })
            .then(async (saved) => {
              await tri.persist()
              return saved
            })
        : api.declareSource(spaceId, branchId, body)
    },
    onSuccess: (saved) => (collectAfter.current ? onDeclaredCollect(source?.slug ?? saved.slug) : onSaved()),
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const disabled =
    save.isPending || !label || !slug || !connectionId || !repository || !branch || parsers.length === 0
  const toggleParser = (id: string) => {
    parsersTouched.current = true
    setParsers((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  }

  return (
    <Modal
      title={editMode ? t('editGitSource') : t('newGitSource')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          {/* En modification aussi (0d85e746) : enregistrer puis collecter
              dans la foulée, sans repasser par la page de la source. */}
          <button
            type="button"
            className="btn"
            disabled={disabled}
            onClick={() => {
              setMessages([])
              collectAfter.current = true
              save.mutate()
            }}
          >
            {editMode ? t('saveAndCollect') : t('declareAndCollect')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={disabled}
            onClick={() => {
              setMessages([])
              collectAfter.current = false
              save.mutate()
            }}
          >
            {editMode ? t('save') : t('declare')}
          </button>
        </>
      }
    >
      <ErrorList messages={messages} />
      <div className="master-detail">
        <div className="stack">
          <div className="form-grid">
            <LabelSlugFields
              idPrefix="g"
              state={pair}
              labelText={t('fieldLabel')}
              slugText={t('fieldSlug')}
              slugLocked={editMode}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g-conn">
              {t('fieldConnection')}
            </label>
            {forges.length > 0 ? (
              <select
                id="g-conn"
                className="select"
                value={connectionId}
                onChange={(e) => {
                  setConnectionId(e.target.value)
                  setRepository('')
                  setBranch('')
                }}
              >
                <option value="">{t('chooseConnection')}</option>
                {forges.map((c) => (
                  <option key={c.id} value={c.id}>
                    {`${c.label} · ${c.identity}`}
                  </option>
                ))}
              </select>
            ) : (
              <span className="muted">{t('noForgeConnections')}</span>
            )}
          </div>
          {/* Listes filtrables : un dépôt Azure en compte des centaines. */}
          <div className="field">
            <label className="field-label" htmlFor="g-repo">
              {t('fieldRepo')}
            </label>
            <Combobox
              id="g-repo"
              disabled={!connectionId}
              options={(repos.data?.repositories ?? []).map((r) => ({ value: r.full_name, label: r.full_name }))}
              value={repository}
              onChange={(value) => {
                setRepository(value)
                setBranch('')
              }}
              placeholder={t('chooseRepo')}
              filterPlaceholder={t('filterRepos')}
              emptyText={t('repoNoMatch')}
              moreText={(count) => t('repoMoreRefine', { count })}
              busy={repos.isFetching}
              busyText={t('repoLoading')}
              status={repos.isError ? <span className="text-danger">{errorMessages(t, repos.error).join(' ')}</span> : null}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g-branch">
              {t('fieldBranch')}
            </label>
            <Combobox
              id="g-branch"
              disabled={!repository}
              options={(branches.data?.branches ?? []).map((b) => ({ value: b, label: b }))}
              value={branch}
              onChange={setBranch}
              placeholder={t('chooseBranch')}
              filterPlaceholder={t('filterBranches')}
              emptyText={t('noOtherBranch')}
              moreText={(count) => t('repoMoreRefine', { count })}
              busy={branches.isFetching}
              busyText={t('branchesLoading')}
              status={
                branches.isError ? <span className="text-danger">{errorMessages(t, branches.error).join(' ')}</span> : null
              }
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g-dirs">
              {t('fieldDirs')}
            </label>
            <input id="g-dirs" className="input" value={dirs} onChange={(e) => setDirs(e.target.value)} />
            <span className="field-hint">{t('dirsHint')}</span>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g-excl">
              {t('fieldExcludedDirs')}
            </label>
            <input
              id="g-excl"
              className="input"
              value={excludedDirs}
              onChange={(e) => setExcludedDirs(e.target.value)}
            />
            <span className="field-hint">{t('excludedDirsHint')}</span>
          </div>
          <div className="field">
            <span className="field-label">{t('fieldParsers')}</span>
            {plugins.length > 0 ? (
              <div className="stack" style={{ gap: 4 }}>
                {plugins.map((plugin) => (
                  <label key={plugin.id} className="row" style={{ gap: 6 }}>
                    <input
                      type="checkbox"
                      checked={parsers.includes(plugin.id)}
                      onChange={() => toggleParser(plugin.id)}
                    />
                    {plugin.label}
                    <span className="muted mono small">
                      {plugin.globs.join(', ')}
                    </span>
                  </label>
                ))}
              </div>
            ) : (
              <span className="muted">{t('noParsers')}</span>
            )}
            <span className="field-hint">{t('parsersHint')}</span>
          </div>
          {editMode ? <TriRulesEditor state={tri} /> : null}
        </div>
        <div className="stack">
          <ManifestPreview manifest={preview} />
          <div className="info-note">{t('sourceSecretNote')}</div>
        </div>
      </div>
    </Modal>
  )
}

function SqlSourceModal({
  spaceId,
  branchId,
  source,
  onClose,
  onSaved,
  onDeclaredCollect,
}: {
  spaceId: string
  branchId: string
  source?: SourceItem
  onClose: () => void
  onSaved: () => void
  onDeclaredCollect: (slug: string) => void
}) {
  const { t } = useTranslation()
  const editMode = source !== undefined
  const p = source?.parameters ?? {}
  // Paire libellé/slug : règle commune (components/LabelSlugFields).
  const pair = useLabelSlug({ label: source?.label, slug: source?.slug })
  const { label } = pair
  const slug = pair.finalSlug
  const [connectionId, setConnectionId] = useState('')
  const [database, setDatabase] = useState(String(p.database ?? ''))
  const [schemas, setSchemas] = useState<string[]>(asList(p.schemas).map(String))
  const [exclusions, setExclusions] = useState(asList(p.exclusions).join(', '))
  const [messages, setMessages] = useState<string[]>([])
  const tri = useTriRules(spaceId, branchId, source?.slug)

  const connections = useQuery({ queryKey: ['db-connections'], queryFn: api.listDbConnections })
  const dbs = useMemo(() => connections.data?.connections ?? [], [connections.data])


  useEffect(() => {
    if (editMode && !connectionId && source?.connection?.slug) {
      const match = dbs.find((c) => c.slug === source.connection?.slug)
      if (match) setConnectionId(match.id)
    }
  }, [editMode, connectionId, dbs, source])

  const chosen = dbs.find((c) => c.id === connectionId)
  const engine = source?.type ?? chosen?.engine ?? ''
  const connSlug = chosen?.slug ?? ''
  const parameters: Record<string, unknown> = {
    database,
    ...(schemas.length > 0 ? { schemas } : {}),
    ...(exclusions.trim()
      ? { exclusions: exclusions.split(',').map((s) => s.trim()).filter(Boolean) }
      : {}),
  }
  const preview = { slug, label, type: engine || 'postgres', connection: connSlug, parameters }

  const collectAfter = useRef(false)
  const save = useMutation({
    mutationFn: () => {
      const body: SourceInput = { slug, label, type: engine, connection: connSlug, parameters }
      return editMode
        ? api
            .updateSource(spaceId, branchId, source!.slug, { label, connection: connSlug, parameters })
            .then(async (saved) => {
              await tri.persist()
              return saved
            })
        : api.declareSource(spaceId, branchId, body)
    },
    onSuccess: (saved) => (collectAfter.current ? onDeclaredCollect(source?.slug ?? saved.slug) : onSaved()),
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const sqlDisabled = save.isPending || !label || !slug || !connectionId || !database
  return (
    <Modal
      title={editMode ? t('editSqlSource') : t('newSqlSource')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          {/* En modification aussi (0d85e746) : enregistrer puis collecter
              dans la foulée, sans repasser par la page de la source. */}
          <button
            type="button"
            className="btn"
            disabled={sqlDisabled}
            onClick={() => {
              setMessages([])
              collectAfter.current = true
              save.mutate()
            }}
          >
            {editMode ? t('saveAndCollect') : t('declareAndCollect')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={sqlDisabled}
            onClick={() => {
              setMessages([])
              collectAfter.current = false
              save.mutate()
            }}
          >
            {editMode ? t('save') : t('declare')}
          </button>
        </>
      }
    >
      <ErrorList messages={messages} />
      <div className="master-detail">
        <div className="stack">
          <div className="form-grid">
            <LabelSlugFields
              idPrefix="s"
              state={pair}
              labelText={t('fieldLabel')}
              slugText={t('fieldSlug')}
              slugLocked={editMode}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="s-conn">
              {t('fieldConnection')}
            </label>
            {dbs.length > 0 ? (
              <select
                id="s-conn"
                className="select"
                value={connectionId}
                onChange={(e) => setConnectionId(e.target.value)}
                disabled={editMode}
              >
                <option value="">{t('chooseConnection')}</option>
                {dbs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {`${c.label} · ${c.engine}`}
                  </option>
                ))}
              </select>
            ) : (
              <span className="muted">{t('noDbConnections')}</span>
            )}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="s-database">
              {t('fieldBase')}
            </label>
            <input id="s-database" className="input" value={database} onChange={(e) => setDatabase(e.target.value)} />
          </div>
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="s-schemas">
                {t('fieldSchemas')}
              </label>
              <SchemaPicker connectionId={connectionId} database={database} value={schemas} onChange={setSchemas} autoLoad={editMode} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="s-excl">
                {t('fieldExclusions')}
              </label>
              <input id="s-excl" className="input" value={exclusions} onChange={(e) => setExclusions(e.target.value)} />
            </div>
          </div>
          {editMode ? <TriRulesEditor state={tri} /> : null}
        </div>
        <div className="stack">
          <ManifestPreview manifest={preview} />
          <div className="info-note">{t('sourceSecretNote')}</div>
        </div>
      </div>
    </Modal>
  )
}
