import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import type {
  CollectReport,
  CollectionRun,
  ContractAnalysis,
  CurationField,
  CurationObjectItem,
  EnrichScope,
  EnrichValues,
  SourceItem,
} from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useWorkspace } from '../workspace'

type Tab = 'overview' | 'contrats' | 'collectes' | 'scan' | 'curation' | 'manifeste' | 'raw'

function sourceMeta(s: SourceItem, t: (k: string) => string): string {
  const p = s.parametres ?? {}
  if (s.type === 'openapi') {
    return `${s.slug} · ${t('openapiKind')}`
  }
  if (s.type === 'git') {
    return `${s.slug} · Git · ${s.connexion?.label ?? ''} · ${String(p.depot ?? '')} @ ${String(p.branche ?? '')}`
  }
  return `${s.slug} · ${s.type} · ${s.connexion?.label ?? ''} · ${String(p.base ?? '')}`
}

export function SourceDetailPage() {
  const { t } = useTranslation()
  const { slug = '' } = useParams()
  const ws = useWorkspace()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<Tab | null>(null)
  const [force, setForce] = useState(false)
  const [report, setReport] = useState<CollectReport | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')

  const ready = !!ws.spaceId && !!ws.branchId
  const sources = useQuery({
    queryKey: ['sources', ws.spaceId, ws.branchId],
    queryFn: () => api.listSources(ws.spaceId as string, ws.branchId as string),
    enabled: ready,
    retry: false,
  })
  const source = sources.data?.sources.find((s) => s.slug === slug)
  const isOpenapi = source?.type === 'openapi'
  const collectable = source?.type === 'git' || isOpenapi
  const tabList: Tab[] = isOpenapi
    ? ['overview', 'contrats', 'collectes', 'scan', 'curation', 'manifeste', 'raw']
    : ['overview', 'collectes', 'scan', 'curation', 'manifeste', 'raw']
  const effectiveTab: Tab = tab ?? (isOpenapi ? 'contrats' : 'collectes')
  const collections = useQuery({
    queryKey: ['collections', ws.spaceId, ws.branchId, slug],
    queryFn: () => api.listCollections(ws.spaceId as string, ws.branchId as string, slug),
    enabled: ready,
    retry: false,
  })

  const collect = useMutation({
    mutationFn: () => api.collectSource(ws.spaceId as string, ws.branchId as string, slug, force),
    onSuccess: async (r) => {
      setReport(r)
      setMessages([])
      setNotice('')
      setTab('collectes')
      await queryClient.invalidateQueries({ queryKey: ['collections'] })
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const publish = useMutation({
    mutationFn: () => api.publishBranch(ws.spaceId as string, ws.branchId as string),
    onSuccess: (r) => setNotice(t('published', { count: r.published })),
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  if (!ready) {
    return (
      <main>
        <div className="empty-note">{t('noBranchYet')}</div>
      </main>
    )
  }

  return (
    <main>
      <div className="breadcrumb">
        {ws.spaces.find((s) => s.id === ws.spaceId)?.name} › {t('navSources')} › {slug}
      </div>
      <div className="page-head">
        <div>
          <h1 className="page-title">{source?.ok ? source.label : slug}</h1>
          {source?.ok ? <p className="page-sub mono">{sourceMeta(source, t)}</p> : null}
        </div>
        <div className="page-actions">
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            {t('forceFullCollect')}
          </label>
          <Link to="/sources" className="btn">
            {t('editSource')}
          </Link>
          <button
            type="button"
            className="btn btn-primary"
            disabled={collect.isPending || !collectable}
            onClick={() => collect.mutate()}
          >
            {t('collect')}
          </button>
        </div>
      </div>

      <div className="tabs">
        {tabList.map((id) => (
          <button
            key={id}
            type="button"
            className={`tab${effectiveTab === id ? ' active' : ''}`}
            aria-disabled={id === 'raw'}
            onClick={() => (id === 'raw' ? undefined : setTab(id))}
          >
            {t(
              id === 'overview'
                ? 'sourceOverview'
                : id === 'contrats'
                  ? 'tabContracts'
                  : id === 'collectes'
                    ? 'tabCollectes'
                    : id === 'scan'
                      ? 'tabScan'
                      : id === 'curation'
                        ? 'tabCuration'
                        : id === 'manifeste'
                          ? 'tabManifeste'
                          : 'tabRawExplorer',
            )}
          </button>
        ))}
      </div>

      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}

      {effectiveTab === 'overview' ? (
        <div className="panel">
          <p className="panel-label">{t('sourceOverview')}</p>
          {source?.ok ? (
            <p className="mono">{sourceMeta(source, t)}</p>
          ) : (
            <p className="muted">{t('sourceInError')}</p>
          )}
        </div>
      ) : null}

      {effectiveTab === 'contrats' ? (
        <ContractsTab
          spaceId={ws.spaceId as string}
          branchId={ws.branchId as string}
          slug={slug}
        />
      ) : null}

      {effectiveTab === 'raw' ? <div className="empty-note">{t('rawExplorerSoon')}</div> : null}

      {effectiveTab === 'scan' ? (
        <ScanPanel spaceId={ws.spaceId as string} branchId={ws.branchId as string} slug={slug} />
      ) : null}

      {effectiveTab === 'curation' ? (
        <CurationTab spaceId={ws.spaceId as string} branchId={ws.branchId as string} slug={slug} />
      ) : null}

      {effectiveTab === 'manifeste' ? (
        <div className="panel">
          <p className="panel-label">{t('manifestTechTitle')}</p>
          <p className="muted">{t('manifestTechNote')}</p>
          {report ? (
            <p className="mono">
              {t('manifestEntries', { count: report.retained })}
            </p>
          ) : null}
        </div>
      ) : null}

      {effectiveTab === 'collectes' ? (
        <div className="master-detail">
          <div className="stack">
            {report ? <ReportCard report={report} onPublish={() => publish.mutate()} publishing={publish.isPending} /> : null}
            {!report ? <div className="empty-note">{t('collectHint')}</div> : null}
          </div>
          <div className="panel">
            <p className="panel-label">{t('collectHistory')}</p>
            {(collections.data?.collections ?? []).length > 0 ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('colDate')}</th>
                    <th>{t('colSourceCommit')}</th>
                    <th>{t('colResult')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(collections.data?.collections ?? []).map((run) => (
                    <tr key={run.id}>
                      <td>
                        {new Date(run.created_at).toLocaleString()}
                        <div className="muted" style={{ fontSize: '0.8rem' }}>
                          {run.author}
                        </div>
                      </td>
                      <td className="mono">{run.source_commit.slice(0, 7)}</td>
                      <td>{runResult(run, t)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">{t('noCollections')}</p>
            )}
          </div>
        </div>
      ) : null}
    </main>
  )
}

function runResult(run: CollectionRun, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (run.local_commit === null) return t('collectNoChange')
  const parts = [`+${run.added}`, `~${run.modified}`, `-${run.deleted}`]
  const base = parts.join(' ')
  return run.error_count > 0 ? `${base} · ${t('collectErrorsCount', { count: run.error_count })}` : base
}

function ReportCard({
  report,
  onPublish,
  publishing,
}: {
  report: CollectReport
  onPublish: () => void
  publishing: boolean
}) {
  const { t } = useTranslation()
  const step = (label: string, detail: string) => (
    <div style={{ marginBottom: 8 }}>
      <strong>{'✓ '}{label}</strong>
      <div className="muted" style={{ fontSize: '0.88rem' }}>
        {detail}
      </div>
    </div>
  )
  const counter = (n: number, label: string, danger = false) => (
    <div className="pill" style={{ padding: '8px 12px', color: danger && n > 0 ? 'var(--danger)' : undefined }}>
      <strong style={{ fontSize: '1.1rem' }}>{n}</strong> {label}
    </div>
  )
  return (
    <div className="panel">
      <p className="panel-label">{t('latestCollection')}</p>
      {step(t('stepBranchUpToDate'), t('stepBranchUpToDateDetail'))}
      {step(t('stepSourceDescended'), t('stepSourceDescendedDetail', { commit: report.source_commit.slice(0, 7) }))}
      {step(
        t('stepRead'),
        t('stepReadDetail', { retained: report.retained, unchanged: report.unchanged, read: report.read }),
      )}
      {step(
        t('stepCommit'),
        report.local_commit
          ? t('stepCommitDetail', { commit: report.local_commit.slice(0, 7) })
          : t('collectNoChange'),
      )}
      <div className="row" style={{ margin: '12px 0' }}>
        {counter(report.added, t('cAdded'))}
        {counter(report.modified, t('cModified'))}
        {counter(report.deleted, t('cDeleted'))}
        {counter(report.unchanged, t('cUnchanged'))}
        {counter(report.errors.length, t('cErrors'), true)}
      </div>
      {report.errors.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>{t('collectErrorFile')}</th>
              <th>{t('colCause')}</th>
            </tr>
          </thead>
          <tbody>
            {report.errors.map((e) => (
              <tr key={e.source}>
                <td className="mono">{e.source}</td>
                <td className="muted">
                  {e.cause} · {t('collectRetried')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <div className="row" style={{ marginTop: 12 }}>
        <span className="btn btn-sm" aria-disabled="true" title={t('rawExplorerSoon')}>
          {t('viewDiff')}
        </span>
        <span className="btn btn-sm" aria-disabled="true" title={t('rawExplorerSoon')}>
          {t('openRawExplorer')}
        </span>
        {report.local_commit ? (
          <button type="button" className="btn btn-sm btn-primary" onClick={onPublish} disabled={publishing}>
            {t('publishBranch')}
          </button>
        ) : null}
      </div>
    </div>
  )
}

type Pending = { filename: string; content: string; analysis: ContractAnalysis }

function ContractsTab({
  spaceId,
  branchId,
  slug,
}: {
  spaceId: string
  branchId: string
  slug: string
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [messages, setMessages] = useState<string[]>([])
  const [pending, setPending] = useState<Pending | null>(null)
  const [version, setVersion] = useState('')

  const contracts = useQuery({
    queryKey: ['contracts', spaceId, branchId, slug],
    queryFn: () => api.listContracts(spaceId, branchId, slug),
    retry: false,
  })
  const list = contracts.data?.contrats ?? []
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['contracts', spaceId, branchId, slug] })

  const analyze = useMutation({
    mutationFn: (file: { filename: string; content: string }) =>
      api
        .analyzeContract(spaceId, branchId, slug, file.filename, file.content)
        .then((analysis) => ({ ...file, analysis })),
    onSuccess: (res) => {
      setPending(res)
      setVersion(res.analysis.version_proposee)
      setMessages([])
    },
    onError: (err) => {
      setPending(null)
      setMessages(errorMessages(t, err))
    },
  })

  const deposit = useMutation({
    mutationFn: (replace: boolean) =>
      api.depositContract(spaceId, branchId, slug, {
        filename: pending?.filename ?? '',
        content: pending?.content ?? '',
        version,
        replace,
      }),
    onSuccess: async () => {
      setPending(null)
      setMessages([])
      await refresh()
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.body.error === 'contract_version_taken') {
        if (window.confirm(t('replaceContractConfirm', { version }))) {
          deposit.mutate(true)
          return
        }
        setMessages([])
        return
      }
      setMessages(errorMessages(t, err))
    },
  })

  const remove = useMutation({
    mutationFn: (v: string) => api.removeContract(spaceId, branchId, slug, v),
    onSuccess: refresh,
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  function onFile(file: File | undefined, forcedVersion?: string): void {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      analyze.mutate({ filename: file.name, content: String(reader.result ?? '') })
      if (forcedVersion) setVersion(forcedVersion)
    }
    reader.readAsText(file)
  }

  const [replacing, setReplacing] = useState('')
  const needsCollect = list.some((c) => !c.collecte)

  return (
    <div className="stack">
      {needsCollect ? <div className="banner">{t('contractsNeedCollect')}</div> : null}
      <ErrorList messages={messages} />
      <input
        ref={fileRef}
        type="file"
        accept=".yaml,.yml,.json,application/json,application/yaml"
        style={{ display: 'none' }}
        onChange={(e) => {
          onFile(e.target.files?.[0], replacing || undefined)
          setReplacing('')
          e.target.value = ''
        }}
      />
      <div className="master-detail">
        <div className="panel">
          <p className="panel-label">{t('contractsDeposited')}</p>
          <p className="muted" style={{ fontSize: '0.84rem' }}>
            {t('contractsPathNote', { slug })}
          </p>
          {list.length > 0 ? (
            <table className="table">
              <thead>
                <tr>
                  <th>{t('colVersion')}</th>
                  <th>{t('colOrigin')}</th>
                  <th>{t('colOpenapi')}</th>
                  <th>{t('colContent')}</th>
                  <th>{t('colDeposited')}</th>
                  <th>{t('colCollectState')}</th>
                  <th aria-label={t('colActions')} />
                </tr>
              </thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.version}>
                    <td>
                      <strong>{c.version}</strong>
                      <div className="muted mono" style={{ fontSize: '0.8rem' }}>
                        {c.fichier}
                      </div>
                    </td>
                    <td className="muted">{c.fichier_origine}</td>
                    <td className="mono">{c.openapi}</td>
                    <td className="muted">
                      {t('schemasOps', { schemas: c.nb_schemas, ops: c.nb_operations })}
                    </td>
                    <td className="muted" style={{ fontSize: '0.82rem' }}>
                      {new Date(c.depose_le).toLocaleDateString()}
                      <div>{c.auteur}</div>
                    </td>
                    <td>
                      {c.collecte ? (
                        <span className="pill" style={{ color: '#2e7d32' }}>
                          {t('contractCollected')}
                        </span>
                      ) : (
                        <span className="pill" style={{ color: 'var(--warn, #9a6700)' }}>
                          {t('contractToCollect')}
                        </span>
                      )}
                    </td>
                    <td>
                      <div className="row">
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            setReplacing(c.version)
                            fileRef.current?.click()
                          }}
                        >
                          {t('replaceContract')}
                        </button>
                        <a
                          className="btn btn-sm"
                          href={api.contractDownloadUrl(spaceId, branchId, slug, c.version)}
                        >
                          {t('downloadContract')}
                        </a>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={() => {
                            if (window.confirm(t('removeContractConfirm', { version: c.version })))
                              remove.mutate(c.version)
                          }}
                        >
                          {t('removeContract')}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty-note">{t('noContracts')}</div>
          )}
          <div className="info-note" style={{ marginTop: 12 }}>
            {t('contractsCoexistNote')}
          </div>
        </div>

        <div className="panel">
          <p className="panel-label">{t('depositContract')}</p>
          <button
            type="button"
            className="dropzone"
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              onFile(e.dataTransfer.files?.[0])
            }}
          >
            <strong>{t('dropContract')}</strong>
            <div className="muted" style={{ fontSize: '0.84rem' }}>
              {t('dropContractHint')}
            </div>
          </button>

          {pending ? (
            <div className="panel" style={{ marginTop: 12 }}>
              <p className="mono" style={{ fontWeight: 600 }}>
                {pending.filename}
              </p>
              <ul style={{ margin: '8px 0', paddingLeft: 18 }}>
                <li>
                  {t('analysisHead', {
                    openapi: pending.analysis.openapi,
                    titre: pending.analysis.titre,
                    version: pending.analysis.version_declaree,
                  })}
                </li>
                <li>
                  {t('schemasOps', {
                    schemas: pending.analysis.nb_schemas,
                    ops: pending.analysis.nb_operations,
                  })}
                </li>
                {pending.analysis.refs_externes.length > 0 ? (
                  <li style={{ color: 'var(--warn, #9a6700)' }}>
                    {t('externalRefs', {
                      count: pending.analysis.refs_externes.length,
                      refs: pending.analysis.refs_externes.join(', '),
                    })}
                  </li>
                ) : null}
              </ul>
              <div className="field">
                <label className="field-label" htmlFor="c-version">
                  {t('saveAsVersion')}
                </label>
                <input
                  id="c-version"
                  className="input mono"
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                />
                <span className="field-hint">{t('saveAsVersionHint')}</span>
              </div>
              <div className="row" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
                <button type="button" className="btn" onClick={() => setPending(null)}>
                  {t('cancelEdit')}
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={deposit.isPending || !version}
                  onClick={() => deposit.mutate(false)}
                >
                  {t('deposit')}
                </button>
              </div>
            </div>
          ) : null}
          <div className="info-note" style={{ marginTop: 12 }}>
            {t('contractsCommitNote')}
          </div>
        </div>
      </div>
    </div>
  )
}

const _STATUT_KEY: Record<string, string> = {
  'traité': 'statutTraite',
  'inchangé': 'statutInchange',
  erreur: 'statutErreur',
  'ignoré': 'statutIgnore',
  en_attente: 'statutEnAttente',
}

function ScanPanel({
  spaceId,
  branchId,
  slug,
}: {
  spaceId: string
  branchId: string
  slug: string
}) {
  const { t } = useTranslation()
  const scan = useQuery({
    queryKey: ['scan', spaceId, branchId, slug],
    queryFn: () => api.listSourceScan(spaceId, branchId, slug),
    retry: false,
  })
  const rows = scan.data?.fichiers ?? []
  return (
    <div className="panel">
      <p className="muted" style={{ fontSize: '0.88rem' }}>
        {t('scanIntro')}
      </p>
      {rows.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>{t('colFile')}</th>
              <th>{t('colCible')}</th>
              <th>{t('colStatut')}</th>
              <th>{t('colCrc')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.path}>
                <td className="mono">{row.path}</td>
                <td className="mono">{row.cible}</td>
                <td>
                  {t(_STATUT_KEY[row.statut] ?? 'statutEnAttente')}
                  {row.message ? (
                    <div className="muted" style={{ fontSize: '0.8rem' }}>
                      {row.message}
                    </div>
                  ) : null}
                </td>
                <td className="mono muted" style={{ fontSize: '0.78rem' }}>
                  {row.crc.slice(0, 10)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty-note">{t('noScan')}</div>
      )}
    </div>
  )
}

const _RETAINED_KEY: Record<string, string> = {
  none: 'retainedNone',
  partial: 'retainedPartial',
  full: 'retainedFull',
}

function CurationTab({
  spaceId,
  branchId,
  slug,
}: {
  spaceId: string
  branchId: string
  slug: string
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'retained' | 'none'>('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [enriching, setEnriching] = useState<string | null>(null)
  const [messages, setMessages] = useState<string[]>([])

  const list = useQuery({
    queryKey: ['curation', spaceId, branchId, slug],
    queryFn: () => api.listCuration(spaceId, branchId, slug),
    retry: false,
  })
  const objects = list.data?.objects ?? []
  const query = search.trim().toLowerCase()
  const filtered = objects.filter((o: CurationObjectItem) => {
    if (query && !o.title.toLowerCase().includes(query) && !o.path.toLowerCase().includes(query)) {
      return false
    }
    if (filter === 'retained' && o.retained === 'none') return false
    if (filter === 'none' && o.retained !== 'none') return false
    return true
  })

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['curation', spaceId, branchId, slug] })

  const retain = useMutation({
    mutationFn: (vars: { path: string; fields: string[] | null }) =>
      api.retainAsset(spaceId, branchId, slug, vars.path, vars.fields),
    onSuccess: async () => {
      setMessages([])
      setSelected(null)
      await invalidate()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const remove = useMutation({
    mutationFn: (path: string) => api.removeAsset(spaceId, branchId, slug, path),
    onSuccess: async () => {
      setMessages([])
      await invalidate()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const refresh = useMutation({
    mutationFn: (paths: string[] | null) => api.refreshAssets(spaceId, branchId, slug, paths),
    onSuccess: async () => {
      setMessages([])
      await invalidate()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const hasRetained = objects.some((o: CurationObjectItem) => o.retained !== 'none')

  return (
    <div className="panel">
      <p className="muted" style={{ fontSize: '0.88rem' }}>
        {t('curationIntro')}
      </p>
      <div className="toolbar" style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('curationSearch')}
          aria-label={t('curationSearch')}
        />
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value as 'all' | 'retained' | 'none')}
          aria-label={t('curationFilter')}
        >
          <option value="all">{t('curationFilterAll')}</option>
          <option value="retained">{t('curationFilterRetained')}</option>
          <option value="none">{t('curationFilterNone')}</option>
        </select>
        <button
          type="button"
          onClick={() => refresh.mutate(null)}
          disabled={refresh.isPending || !hasRetained}
          style={{ marginLeft: 'auto' }}
        >
          {t('curationRefreshAll')}
        </button>
      </div>

      <ErrorList messages={messages} />

      {filtered.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>{t('curationColObject')}</th>
              <th>{t('curationColKind')}</th>
              <th>{t('curationColFields')}</th>
              <th>{t('curationColRetained')}</th>
              <th>{t('curationColActions')}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((o) => (
              <tr key={o.path}>
                <td>
                  {o.title}
                  {o.orphan ? (
                    <span className="badge badge-warn" style={{ marginLeft: '0.4rem' }}>
                      {t('curationOrphan')}
                    </span>
                  ) : null}
                  <div className="mono muted" style={{ fontSize: '0.78rem' }}>
                    {o.path}
                  </div>
                </td>
                <td className="mono">{o.kind}</td>
                <td>{o.fields}</td>
                <td>{t(_RETAINED_KEY[o.retained] ?? 'retainedNone')}</td>
                <td>
                  {o.orphan ? (
                    <>
                      <button type="button" onClick={() => setEnriching(o.path)}>
                        {t('curationEnrich')}
                      </button>{' '}
                      <button
                        type="button"
                        onClick={() => {
                          if (window.confirm(t('curationConfirmRemove'))) remove.mutate(o.path)
                        }}
                        disabled={remove.isPending}
                      >
                        {t('curationRemove')}
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => retain.mutate({ path: o.path, fields: null })}
                        disabled={retain.isPending}
                      >
                        {t('curationRetainAll')}
                      </button>{' '}
                      <button type="button" onClick={() => setSelected(o.path)}>
                        {t('curationChooseFields')}
                      </button>{' '}
                      {o.retained !== 'none' ? (
                        <>
                          <button type="button" onClick={() => setEnriching(o.path)}>
                            {t('curationEnrich')}
                          </button>{' '}
                          <button
                            type="button"
                            onClick={() => refresh.mutate([o.path])}
                            disabled={refresh.isPending}
                          >
                            {t('curationRefresh')}
                          </button>{' '}
                          <button
                            type="button"
                            onClick={() => {
                              if (window.confirm(t('curationConfirmRemove'))) remove.mutate(o.path)
                            }}
                            disabled={remove.isPending}
                          >
                            {t('curationRemove')}
                          </button>
                        </>
                      ) : null}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty-note">{t('curationEmpty')}</div>
      )}

      {selected ? (
        <FieldPanel
          spaceId={spaceId}
          branchId={branchId}
          slug={slug}
          path={selected}
          onClose={() => setSelected(null)}
          onSave={(fields) => retain.mutate({ path: selected, fields })}
          pending={retain.isPending}
        />
      ) : null}

      {enriching ? (
        <EnrichPanel
          spaceId={spaceId}
          branchId={branchId}
          slug={slug}
          path={enriching}
          onClose={() => setEnriching(null)}
          onSaved={() => {
            setEnriching(null)
            void invalidate()
          }}
        />
      ) : null}
    </div>
  )
}

function FieldPanel({
  spaceId,
  branchId,
  slug,
  path,
  onClose,
  onSave,
  pending,
}: {
  spaceId: string
  branchId: string
  slug: string
  path: string
  onClose: () => void
  onSave: (fields: string[]) => void
  pending: boolean
}) {
  const { t } = useTranslation()
  const detail = useQuery({
    queryKey: ['curationObject', spaceId, branchId, slug, path],
    queryFn: () => api.getCurationObject(spaceId, branchId, slug, path),
    retry: false,
  })
  const fields = detail.data?.fields ?? []
  const [checked, setChecked] = useState<Record<string, boolean> | null>(null)
  const current: Record<string, boolean> =
    checked ?? Object.fromEntries(fields.map((f: CurationField) => [f.name, f.retained]))

  return (
    <div className="panel" style={{ marginTop: '0.75rem' }}>
      <p className="panel-label">
        {t('curationFieldsFor', { title: detail.data?.title ?? path })}
      </p>
      {fields.length > 0 ? (
        <ul style={{ listStyle: 'none', padding: 0 }}>
          {fields.map((f) =>
            f.missing ? (
              <li key={f.name} className="muted">
                <span className="mono">{f.name}</span>{' '}
                <span style={{ fontSize: '0.8rem' }}>{f.type}</span>{' '}
                <span className="badge badge-warn">{t('curationFieldMissing')}</span>
              </li>
            ) : (
              <li key={f.name}>
                <label>
                  <input
                    type="checkbox"
                    checked={current[f.name] ?? false}
                    onChange={() => setChecked({ ...current, [f.name]: !current[f.name] })}
                  />{' '}
                  <span className="mono">{f.name}</span>{' '}
                  <span className="muted" style={{ fontSize: '0.8rem' }}>
                    {f.type}
                  </span>{' '}
                  {f.ref ? <span className="badge">{t('curationFieldRef')}</span> : null}
                </label>
              </li>
            ),
          )}
        </ul>
      ) : (
        <p className="muted">{t('curationNoFields')}</p>
      )}
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button
          type="button"
          onClick={() =>
            onSave(fields.filter((f) => !f.missing && current[f.name]).map((f) => f.name))
          }
          disabled={pending}
        >
          {t('curationSaveFields')}
        </button>
        <button type="button" onClick={onClose}>
          {t('cancel')}
        </button>
      </div>
    </div>
  )
}

type ScopeForm = { business_name: string; description: string; concept: string; tags: string }

function _toValues(form: ScopeForm): EnrichValues {
  return {
    business_name: form.business_name,
    description: form.description,
    concept: form.concept,
    tags: form.tags
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
  }
}

function _initScope(scope: EnrichScope): ScopeForm {
  return {
    business_name: scope.business_name,
    description: scope.description,
    concept: scope.concept,
    tags: scope.tags.join(', '),
  }
}

function EnrichPanel({
  spaceId,
  branchId,
  slug,
  path,
  onClose,
  onSaved,
}: {
  spaceId: string
  branchId: string
  slug: string
  path: string
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const [form, setForm] = useState<Record<string, ScopeForm> | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const view = useQuery({
    queryKey: ['enrichment', spaceId, branchId, slug, path],
    queryFn: () => api.getEnrichment(spaceId, branchId, slug, path),
    retry: false,
  })

  const scopes: EnrichScope[] = view.data ? [view.data.object, ...view.data.fields] : []
  const current: Record<string, ScopeForm> =
    form ?? Object.fromEntries(scopes.map((s) => [s.name, _initScope(s)]))

  const save = useMutation({
    mutationFn: () =>
      api.enrichAsset(spaceId, branchId, slug, {
        path,
        obj: current[''] ? _toValues(current['']) : null,
        fields: Object.fromEntries(
          (view.data?.fields ?? []).map((f) => [f.name, _toValues(current[f.name])]),
        ),
      }),
    onSuccess: () => {
      setMessages([])
      onSaved()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const update = (name: string, key: keyof ScopeForm, value: string) =>
    setForm({ ...current, [name]: { ...current[name], [key]: value } })

  const editor = (scope: EnrichScope, label: string) => {
    const f = current[scope.name]
    const readonlyKeys = Object.keys(scope.readonly)
    return (
      <div key={scope.name || '(object)'} className="panel" style={{ marginBottom: '0.5rem' }}>
        <p className="panel-label">{label}</p>
        <div style={{ display: 'grid', gap: '0.35rem' }}>
          <input
            value={f.business_name}
            onChange={(e) => update(scope.name, 'business_name', e.target.value)}
            placeholder={t('enrichBusinessName')}
            aria-label={t('enrichBusinessName')}
          />
          <input
            value={f.description}
            onChange={(e) => update(scope.name, 'description', e.target.value)}
            placeholder={t('enrichDescription')}
            aria-label={t('enrichDescription')}
          />
          <input
            value={f.concept}
            onChange={(e) => update(scope.name, 'concept', e.target.value)}
            placeholder={t('enrichConcept')}
            aria-label={t('enrichConcept')}
          />
          <input
            value={f.tags}
            onChange={(e) => update(scope.name, 'tags', e.target.value)}
            placeholder={t('enrichTags')}
            aria-label={t('enrichTags')}
          />
        </div>
        {readonlyKeys.length > 0 ? (
          <p className="muted" style={{ fontSize: '0.78rem', marginTop: '0.3rem' }}>
            {t('enrichReadonly')}: {readonlyKeys.join(', ')}
          </p>
        ) : null}
      </div>
    )
  }

  return (
    <div className="panel" style={{ marginTop: '0.75rem' }}>
      <p className="panel-label">{t('enrichTitle', { title: view.data?.title ?? path })}</p>
      <ErrorList messages={messages} />
      {view.data ? (
        <>
          {editor(view.data.object, t('enrichObjectScope'))}
          {view.data.fields.map((field) => editor(field, field.name))}
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="button" onClick={() => save.mutate()} disabled={save.isPending}>
              {t('enrichSave')}
            </button>
            <button type="button" onClick={onClose}>
              {t('cancel')}
            </button>
          </div>
        </>
      ) : (
        <p className="muted">{t('loading')}</p>
      )}
    </div>
  )
}
