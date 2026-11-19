import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../apiClient'
import type { SourceInput, SourceItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useWorkspace } from '../workspace'

function slugify(label: string): string {
  return label
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

function whatIsRead(source: SourceItem, t: (k: string) => string): string {
  const p = source.parametres ?? {}
  if (source.type === 'openapi') {
    return t('openapiContractsRead')
  }
  if (source.type === 'git') {
    const dirs = asList(p.repertoires)
    const where = dirs.length > 0 ? dirs.join(', ') : t('wholeRepo')
    return `${String(p.depot ?? '')} @ ${String(p.branche ?? '')} · ${where}`
  }
  const schemas = asList(p.schemas)
  return schemas.length > 0 ? `${String(p.base ?? '')} · ${schemas.join(', ')}` : String(p.base ?? '')
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

  const key = ['sources', ws.spaceId, ws.branchId]
  const sources = useQuery({
    queryKey: key,
    queryFn: () => api.listSources(ws.spaceId as string, ws.branchId as string),
    enabled: !!ws.spaceId && !!ws.branchId,
    retry: false,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['sources'] })

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
        <div className="empty-note">{t('noBranchYet')}</div>
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

      <div className="tabs">
        <span className="tab active">{t('tabSourcesDeclared')}</span>
        <span className="tab" aria-disabled="true" title={t('rawExplorerSoon')}>
          {t('tabRawExplorer')}
        </span>
      </div>

      <ErrorList messages={messages} />

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
                <tr key={source.slug}>
                  <td>
                    {source.ok ? (
                      <>
                        <Link to={`/sources/${source.slug}`} className="link-strong">
                          {source.label}
                        </Link>
                        <div className="muted mono" style={{ fontSize: '0.82rem' }}>
                          {source.slug}
                        </div>
                      </>
                    ) : (
                      <span className="mono">{source.slug}</span>
                    )}
                  </td>
                  <td>
                    {source.ok ? (
                      <>
                        {source.type}
                        <div className="muted" style={{ fontSize: '0.82rem' }}>
                          {source.connexion?.kind === 'missing'
                            ? t('connectionMissing')
                            : source.connexion?.kind === 'none'
                              ? t('sourceNoConnection')
                              : source.connexion?.label}
                        </div>
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="muted">{source.ok ? whatIsRead(source, t) : ''}</td>
                  <td>
                    {source.ok ? (
                      <span className="pill" style={{ color: '#2e7d32' }}>
                        {t('sourceValid')}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--danger)' }}>
                        {t(`fields.${source.error}`, {
                          defaultValue: t('sourceInError'),
                        })}
                      </span>
                    )}
                  </td>
                  <td>
                    <div className="row">
                      {source.ok && (source.type === 'git' || source.type === 'openapi') ? (
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
          onDeclaredCollect={async (slug) => {
            setEditing(null)
            await invalidate()
            navigate(`/sources/${slug}`)
          }}
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
  const [label, setLabel] = useState(source?.label ?? '')
  const [slug, setSlug] = useState(source?.slug ?? '')
  const [slugTouched, setSlugTouched] = useState(editMode)
  const [messages, setMessages] = useState<string[]>([])

  useEffect(() => {
    if (!slugTouched) setSlug(slugify(label))
  }, [label, slugTouched])

  const save = useMutation({
    mutationFn: () => {
      const body: SourceInput = {
        slug,
        label,
        type: 'openapi',
        connexion: '',
        parametres: {},
      }
      return editMode
        ? api.updateSource(spaceId, branchId, source!.slug, {
            label,
            connexion: '',
            parametres: {},
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
          <div className="field">
            <label className="field-label" htmlFor="o-label">
              {t('fieldLabel')}
            </label>
            <input id="o-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="o-slug">
              {t('fieldSlug')}
            </label>
            <input
              id="o-slug"
              className="input mono"
              value={slug}
              disabled={editMode}
              onChange={(e) => {
                setSlugTouched(true)
                setSlug(e.target.value)
              }}
            />
          </div>
        </div>
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
          background: '#0f1b2d',
          color: '#c7d0dd',
          padding: 14,
          borderRadius: 8,
          overflow: 'auto',
          fontSize: '0.82rem',
        }}
      >
        {`version: 1\n${lines.join('\n')}`}
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
  const p = source?.parametres ?? {}
  const [label, setLabel] = useState(source?.label ?? '')
  const [slug, setSlug] = useState(source?.slug ?? '')
  const [slugTouched, setSlugTouched] = useState(editMode)
  // On garde l'id de la connexion dans l'état (pour lister dépôts et branches) ;
  // le manifeste, lui, reçoit le slug.
  const [connexion, setConnexion] = useState('')
  const [depot, setDepot] = useState(String(p.depot ?? ''))
  const [branche, setBranche] = useState(String(p.branche ?? ''))
  const [dirs, setDirs] = useState(asList(p.repertoires).join(', '))
  const [messages, setMessages] = useState<string[]>([])

  const connections = useQuery({ queryKey: ['connections'], queryFn: api.listConnections })
  const repos = useQuery({
    queryKey: ['repos', connexion],
    queryFn: () => api.listConnectionRepositories(connexion),
    enabled: !!connexion,
    retry: false,
  })
  const branches = useQuery({
    queryKey: ['forge-branches', connexion, depot],
    queryFn: () => api.listConnectionBranches(connexion, depot),
    enabled: !!connexion && !!depot,
    retry: false,
  })

  const forges = useMemo(() => connections.data?.connections ?? [], [connections.data])

  useEffect(() => {
    if (!slugTouched) setSlug(slugify(label))
  }, [label, slugTouched])

  // Édition : retrouver l'id de la connexion à partir du slug mémorisé.
  useEffect(() => {
    if (editMode && !connexion && source?.connexion?.slug) {
      const match = forges.find((c) => c.slug === source.connexion?.slug)
      if (match) setConnexion(match.id)
    }
  }, [editMode, connexion, forges, source])

  const parametres: Record<string, unknown> = {
    depot,
    branche,
    ...(dirs.trim()
      ? { repertoires: dirs.split(',').map((d) => d.trim()).filter(Boolean) }
      : {}),
  }
  const connSlug = forges.find((c) => c.id === connexion)?.slug ?? ''
  const preview = { slug, label, type: 'git', connexion: connSlug, parametres }

  const collectAfter = useRef(false)
  const save = useMutation({
    mutationFn: () => {
      const body: SourceInput = { slug, label, type: 'git', connexion: connSlug, parametres }
      return editMode
        ? api.updateSource(spaceId, branchId, source!.slug, {
            label,
            connexion: connSlug,
            parametres,
          })
        : api.declareSource(spaceId, branchId, body)
    },
    onSuccess: (saved) => (collectAfter.current ? onDeclaredCollect(saved.slug) : onSaved()),
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const disabled = save.isPending || !label || !slug || !connexion || !depot || !branche

  return (
    <Modal
      title={editMode ? t('editGitSource') : t('newGitSource')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          {!editMode ? (
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
              {t('declareAndCollect')}
            </button>
          ) : null}
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
            {t('declare')}
          </button>
        </>
      }
    >
      <ErrorList messages={messages} />
      <div className="master-detail">
        <div className="stack">
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="g-label">
                {t('fieldLabel')}
              </label>
              <input id="g-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="g-slug">
                {t('fieldSlug')}
              </label>
              <input
                id="g-slug"
                className="input mono"
                value={slug}
                disabled={editMode}
                onChange={(e) => {
                  setSlugTouched(true)
                  setSlug(e.target.value)
                }}
              />
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g-conn">
              {t('fieldConnection')}
            </label>
            {forges.length > 0 ? (
              <select
                id="g-conn"
                className="select"
                value={connexion}
                onChange={(e) => {
                  setConnexion(e.target.value)
                  setDepot('')
                  setBranche('')
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
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="g-repo">
                {t('fieldRepo')}
              </label>
              <select
                id="g-repo"
                className="select"
                value={depot}
                disabled={!connexion}
                onChange={(e) => {
                  setDepot(e.target.value)
                  setBranche('')
                }}
              >
                <option value="">{t('chooseRepo')}</option>
                {(repos.data?.repositories ?? []).map((r) => (
                  <option key={r.full_name} value={r.full_name}>
                    {r.full_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="g-branch">
                {t('fieldBranch')}
              </label>
              <select
                id="g-branch"
                className="select"
                value={branche}
                disabled={!depot}
                onChange={(e) => setBranche(e.target.value)}
              >
                <option value="">{t('chooseBranch')}</option>
                {(branches.data?.branches ?? []).map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g-dirs">
              {t('fieldDirs')}
            </label>
            <input id="g-dirs" className="input" value={dirs} onChange={(e) => setDirs(e.target.value)} />
            <span className="field-hint">{t('dirsHint')}</span>
          </div>
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
}: {
  spaceId: string
  branchId: string
  source?: SourceItem
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const editMode = source !== undefined
  const p = source?.parametres ?? {}
  const [label, setLabel] = useState(source?.label ?? '')
  const [slug, setSlug] = useState(source?.slug ?? '')
  const [slugTouched, setSlugTouched] = useState(editMode)
  const [connexion, setConnexion] = useState('')
  const [base, setBase] = useState(String(p.base ?? ''))
  const [schemas, setSchemas] = useState(asList(p.schemas).join(', '))
  const [exclusions, setExclusions] = useState(asList(p.exclusions).join(', '))
  const [messages, setMessages] = useState<string[]>([])

  const connections = useQuery({ queryKey: ['db-connections'], queryFn: api.listDbConnections })
  const dbs = useMemo(() => connections.data?.connections ?? [], [connections.data])

  useEffect(() => {
    if (!slugTouched) setSlug(slugify(label))
  }, [label, slugTouched])

  useEffect(() => {
    if (editMode && !connexion && source?.connexion?.slug) {
      const match = dbs.find((c) => c.slug === source.connexion?.slug)
      if (match) setConnexion(match.id)
    }
  }, [editMode, connexion, dbs, source])

  const chosen = dbs.find((c) => c.id === connexion)
  const engine = source?.type ?? chosen?.engine ?? ''
  const connSlug = chosen?.slug ?? ''
  const parametres: Record<string, unknown> = {
    base,
    ...(schemas.trim() ? { schemas: schemas.split(',').map((s) => s.trim()).filter(Boolean) } : {}),
    ...(exclusions.trim()
      ? { exclusions: exclusions.split(',').map((s) => s.trim()).filter(Boolean) }
      : {}),
  }
  const preview = { slug, label, type: engine || 'postgres', connexion: connSlug, parametres }

  const save = useMutation({
    mutationFn: () => {
      const body: SourceInput = { slug, label, type: engine, connexion: connSlug, parametres }
      return editMode
        ? api.updateSource(spaceId, branchId, source!.slug, { label, connexion: connSlug, parametres })
        : api.declareSource(spaceId, branchId, body)
    },
    onSuccess: onSaved,
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  return (
    <Modal
      title={editMode ? t('editSqlSource') : t('newSqlSource')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={save.isPending || !label || !slug || !connexion || !base}
            onClick={() => {
              setMessages([])
              save.mutate()
            }}
          >
            {t('declare')}
          </button>
        </>
      }
    >
      <ErrorList messages={messages} />
      <div className="master-detail">
        <div className="stack">
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="s-label">
                {t('fieldLabel')}
              </label>
              <input id="s-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="s-slug">
                {t('fieldSlug')}
              </label>
              <input
                id="s-slug"
                className="input mono"
                value={slug}
                disabled={editMode}
                onChange={(e) => {
                  setSlugTouched(true)
                  setSlug(e.target.value)
                }}
              />
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="s-conn">
              {t('fieldConnection')}
            </label>
            {dbs.length > 0 ? (
              <select
                id="s-conn"
                className="select"
                value={connexion}
                onChange={(e) => setConnexion(e.target.value)}
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
            <label className="field-label" htmlFor="s-base">
              {t('fieldBase')}
            </label>
            <input id="s-base" className="input" value={base} onChange={(e) => setBase(e.target.value)} />
          </div>
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="s-schemas">
                {t('fieldSchemas')}
              </label>
              <input id="s-schemas" className="input" value={schemas} onChange={(e) => setSchemas(e.target.value)} />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="s-excl">
                {t('fieldExclusions')}
              </label>
              <input id="s-excl" className="input" value={exclusions} onChange={(e) => setExclusions(e.target.value)} />
            </div>
          </div>
        </div>
        <div className="stack">
          <ManifestPreview manifest={preview} />
          <div className="info-note">{t('sourceSecretNote')}</div>
        </div>
      </div>
    </Modal>
  )
}
