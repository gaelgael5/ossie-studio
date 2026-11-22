import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import type {
  CollectReport,
  ContractAnalysis,
  CurationField,
  CurationObjectItem,
  EnrichScope,
  EnrichValues,
  ReviewFieldRef,
  ReviewObjectItem,
  ReviewResult,
  SourceItem,
  TriRule,
} from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { BusyButton } from '../components/BusyButton'
import { useSourceCollect } from '../useSourceCollect'
import { CollectChecklist } from '../components/CollectChecklist'
import { CollectErrorCause, splitScanMessage } from '../components/CollectErrorCause'
import { useWorkspace } from '../workspace'
import { exclusionsOf } from '../sourceExclusions'

const LINKABLE_TABS: string[] = ['overview', 'contrats', 'collectes', 'scan', 'curation', 'revue', 'manifeste']

type Tab =
  | 'overview'
  | 'contrats'
  | 'collectes'
  | 'scan'
  | 'curation'
  | 'revue'
  | 'manifeste'
  | 'raw'

function sourceMeta(s: SourceItem, t: (k: string) => string): string {
  const p = s.parameters ?? {}
  if (s.type === 'openapi') {
    return `${s.slug} · ${t('openapiKind')}`
  }
  if (s.type === 'git') {
    const excluded = exclusionsOf(p)
    const suffix = excluded.length > 0 ? ` · ${t('excludedDirsShort')} ${excluded.join(', ')}` : ''
    return `${s.slug} · Git · ${s.connection?.label ?? ''} · ${String(p.repository ?? '')} @ ${String(p.branch ?? '')}${suffix}`
  }
  return `${s.slug} · ${s.type} · ${s.connection?.label ?? ''} · ${String(p.database ?? '')}`
}

export function SourceDetailPage() {
  const { t } = useTranslation()
  const { slug = '' } = useParams()
  const navigate = useNavigate()
  // L'explorateur raw est un onglet de la page « Sources » : on l'ouvre sur
  // cette source plutôt que de dupliquer l'écran ici.
  const rawExplorerHref = `/sources?tab=raw&source=${encodeURIComponent(slug)}`
  const ws = useWorkspace()
  // L'onglet et l'objet peuvent venir de l'adresse (?tab=curation&object=…) :
  // l'explorateur raw y renvoie (« Voir dans la curation », « diff depuis la revue »).
  const [searchParams] = useSearchParams()
  const [tab, setTab] = useState<Tab | null>(() => {
    const asked = searchParams.get('tab')
    return asked && LINKABLE_TABS.includes(asked as Tab) ? (asked as Tab) : null
  })
  const [force, setForce] = useState(false)
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
    ? ['overview', 'contrats', 'collectes', 'scan', 'curation', 'revue', 'manifeste', 'raw']
    : ['overview', 'collectes', 'scan', 'curation', 'revue', 'manifeste', 'raw']
  const effectiveTab: Tab = tab ?? (isOpenapi ? 'contrats' : 'collectes')
  const collections = useQuery({
    queryKey: ['collections', ws.spaceId, ws.branchId, slug],
    queryFn: () => api.listCollections(ws.spaceId as string, ws.branchId as string, slug),
    enabled: ready,
    retry: false,
  })

  // Collecte en tâche de fond : suivie par le serveur, elle survit à un rechargement.
  const collect = useSourceCollect(ws.spaceId, ws.branchId, slug)
  const report = collect.report
  // La collecte va dans mon brouillon : on la publie depuis l'écran de publication.
  const publishHref = `/spaces/${ws.spaceId}/branches/${ws.branchId}/publish`

  if (!ready) {
    return (
      <main>
        <NoBranchNote spaceId={ws.spaceId} />
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
          <BusyButton
            type="button"
            className="btn btn-primary"
            busy={collect.running}
            busyText={
              collect.startedAt
                ? t('collectRunningSince', { time: new Date(collect.startedAt).toLocaleTimeString() })
                : t('collectRunning')
            }
            disabled={!collectable}
            onClick={() => {
              setMessages([])
              setNotice('')
              setTab('collectes')
              collect.start(force)
            }}
          >
            {t('collect')}
          </BusyButton>
        </div>
      </div>

      <div className="tabs">
        {tabList.map((id) => (
          <button
            key={id}
            type="button"
            className={`tab${effectiveTab === id ? ' active' : ''}`}
            onClick={() => (id === 'raw' ? navigate(rawExplorerHref) : setTab(id))}
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
                        : id === 'revue'
                          ? 'tabReview'
                          : id === 'manifeste'
                            ? 'tabManifeste'
                            : 'tabRawExplorer',
            )}
          </button>
        ))}
      </div>

      <ErrorList messages={[...messages, ...collect.messages]} />
      {collect.progress && (collect.running || collect.state === 'failed') ? (
        <div className="page-section">
          <CollectChecklist
            steps={
              source?.type === 'openapi'
                ? ['scan', 'process', 'resolve', 'write']
                : source?.type === 'git'
                  ? ['download', 'scan', 'process', 'resolve', 'write']
                  : ['download', 'process', 'resolve', 'write']
            }
            progress={collect.progress}
            sourceType={source?.type ?? ''}
          />
        </div>
      ) : null}
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

      {effectiveTab === 'scan' ? (
        <ScanPanel spaceId={ws.spaceId as string} branchId={ws.branchId as string} slug={slug} />
      ) : null}

      {effectiveTab === 'curation' ? (
        <CurationTab
          spaceId={ws.spaceId as string}
          branchId={ws.branchId as string}
          slug={slug}
          initialObject={searchParams.get('object')}
        />
      ) : null}

      {effectiveTab === 'revue' ? (
        <ReviewTab spaceId={ws.spaceId as string} branchId={ws.branchId as string} slug={slug} />
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
            {report ? <ReportCard report={report} publishHref={publishHref} rawExplorerHref={rawExplorerHref} /> : null}
            {!report ? <div className="empty-note">{t('collectHint')}</div> : null}
          </div>
          <div className="panel">
            <p className="panel-label">{t('collectHistory')}</p>
            {(collections.data?.collections ?? []).length > 0 ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('colDate')}</th>
                    <th>{t('colCommit')}</th>
                    <th>{t('colMessage')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(collections.data?.collections ?? []).map((run) => (
                    <tr key={run.sha}>
                      <td>
                        {new Date(run.date).toLocaleString()}
                        <div className="muted small">
                          {run.author}
                        </div>
                      </td>
                      <td className="mono">{run.sha.slice(0, 7)}</td>
                      <td>{run.message}</td>
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

function ReportCard({
  report,
  publishHref,
  rawExplorerHref,
}: {
  report: CollectReport
  publishHref: string
  rawExplorerHref: string
}) {
  const { t } = useTranslation()
  const step = (label: string, detail: string) => (
    <div style={{ marginBottom: 8 }}>
      <strong>{'✓ '}{label}</strong>
      <div className="muted small">
        {detail}
      </div>
    </div>
  )
  const counter = (n: number, label: string, danger = false) => (
    <div className={`pill${danger && n > 0 ? ' text-danger' : ''}`} style={{ padding: '8px 12px' }}>
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
                <td>
                  <CollectErrorCause cause={e.cause} detail={e.detail} />
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
        <Link to={rawExplorerHref} className="btn btn-sm">
          {t('openRawExplorer')}
        </Link>
        {report.local_commit ? (
          <Link to={publishHref} className="btn btn-sm btn-primary">
            {t('publishBranch')}
          </Link>
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
  const list = contracts.data?.contracts ?? []
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['contracts', spaceId, branchId, slug] })

  const analyze = useMutation({
    mutationFn: (file: { filename: string; content: string }) =>
      api
        .analyzeContract(spaceId, branchId, slug, file.filename, file.content)
        .then((analysis) => ({ ...file, analysis })),
    onSuccess: (res) => {
      setPending(res)
      setVersion(res.analysis.proposed_version)
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
  const needsCollect = list.some((c) => !c.collected)

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
          <p className="muted small">
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
                      <div className="muted mono small">
                        {c.file}
                      </div>
                    </td>
                    <td className="muted">{c.original_file}</td>
                    <td className="mono">{c.openapi}</td>
                    <td className="muted">
                      {t('schemasOps', { schemas: c.schema_count, ops: c.operation_count })}
                    </td>
                    <td className="muted small">
                      {new Date(c.uploaded_at).toLocaleDateString()}
                      <div>{c.author}</div>
                    </td>
                    <td>
                      {c.collected ? (
                        <span className="pill text-ok">
                          {t('contractCollected')}
                        </span>
                      ) : (
                        <span className="pill text-warn">
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
            <div className="muted small">
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
                    titre: pending.analysis.title,
                    version: pending.analysis.declared_version,
                  })}
                </li>
                <li>
                  {t('schemasOps', {
                    schemas: pending.analysis.schema_count,
                    ops: pending.analysis.operation_count,
                  })}
                </li>
                {pending.analysis.external_refs.length > 0 ? (
                  <li className="text-warn">
                    {t('externalRefs', {
                      count: pending.analysis.external_refs.length,
                      refs: pending.analysis.external_refs.join(', '),
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
      <p className="muted small">
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
                  {row.message ? <CollectErrorCause {...splitScanMessage(row.message)} /> : null}
                </td>
                <td className="mono muted small">
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
  initialObject = null,
}: {
  spaceId: string
  branchId: string
  slug: string
  initialObject?: string | null
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'retained' | 'none'>('all')
  const [selected, setSelected] = useState<string | null>(initialObject)
  const [enriching, setEnriching] = useState<string | null>(null)
  const [showExcluded, setShowExcluded] = useState(false)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [messages, setMessages] = useState<string[]>([])

  const list = useQuery({
    queryKey: ['curation', spaceId, branchId, slug, showExcluded],
    queryFn: () => api.listCuration(spaceId, branchId, slug, showExcluded),
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
    <div className="panel stack">
      <p className="muted small">{t('curationIntro')}</p>
      <div className="toolbar">
        <input
          type="search"
          className="input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t('curationSearch')}
          aria-label={t('curationSearch')}
        />
        <select
          className="select"
          value={filter}
          onChange={(e) => setFilter(e.target.value as 'all' | 'retained' | 'none')}
          aria-label={t('curationFilter')}
        >
          <option value="all">{t('curationFilterAll')}</option>
          <option value="retained">{t('curationFilterRetained')}</option>
          <option value="none">{t('curationFilterNone')}</option>
        </select>
        <label className="check">
          <input
            type="checkbox"
            checked={showExcluded}
            onChange={(e) => setShowExcluded(e.target.checked)}
          />
          {t('curationShowExcluded')}
        </label>
        <button type="button" className="btn" onClick={() => setRulesOpen((v) => !v)}>
          {t('curationRules')}
        </button>
        <button
          type="button"
          className="btn push-right"
          onClick={() => refresh.mutate(null)}
          disabled={refresh.isPending || !hasRetained}
        >
          {t('curationRefreshAll')}
        </button>
      </div>

      {rulesOpen ? (
        <RulesPanel spaceId={spaceId} branchId={branchId} slug={slug} onSaved={invalidate} />
      ) : null}

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
                  <div className="row">
                    <span>{o.title}</span>
                    {o.orphan ? <span className="badge badge-warn">{t('curationOrphan')}</span> : null}
                    {o.excluded ? <span className="badge badge-muted">{t('curationExcluded')}</span> : null}
                  </div>
                  <div className="mono muted small">
                    {o.path}
                  </div>
                  {o.excluded_by ? (
                    <div className="muted small">
                      {o.excluded && !o.orphan
                        ? `${t('curationExcludedBy')}: ${o.excluded_by}`
                        : `${t('curationRuleConflict')}: ${o.excluded_by}`}
                    </div>
                  ) : null}
                </td>
                <td className="mono">{o.kind}</td>
                <td>{o.fields}</td>
                <td>{t(_RETAINED_KEY[o.retained] ?? 'retainedNone')}</td>
                <td>
                  {o.orphan ? (
                    <div className="row">
                      <button type="button" className="btn btn-sm" onClick={() => setEnriching(o.path)}>
                        {t('curationEnrich')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        onClick={() => {
                          if (window.confirm(t('curationConfirmRemove'))) remove.mutate(o.path)
                        }}
                        disabled={remove.isPending}
                      >
                        {t('curationRemove')}
                      </button>
                    </div>
                  ) : (
                    <div className="row">
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => retain.mutate({ path: o.path, fields: null })}
                        disabled={retain.isPending}
                      >
                        {t('curationRetainAll')}
                      </button>
                      <button type="button" className="btn btn-sm" onClick={() => setSelected(o.path)}>
                        {t('curationChooseFields')}
                      </button>
                      {o.retained !== 'none' ? (
                        <>
                          <button type="button" className="btn btn-sm" onClick={() => setEnriching(o.path)}>
                            {t('curationEnrich')}
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => refresh.mutate([o.path])}
                            disabled={refresh.isPending}
                          >
                            {t('curationRefresh')}
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            onClick={() => {
                              if (window.confirm(t('curationConfirmRemove'))) remove.mutate(o.path)
                            }}
                            disabled={remove.isPending}
                          >
                            {t('curationRemove')}
                          </button>
                        </>
                      ) : null}
                    </div>
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
    <div className="panel stack">
      <p className="panel-label">
        {t('curationFieldsFor', { title: detail.data?.title ?? path })}
      </p>
      {fields.length > 0 ? (
        <ul className="list-plain stack stack-tight">
          {fields.map((f) =>
            f.missing ? (
              <li key={f.name} className="muted">
                <span className="mono">{f.name}</span>{' '}
                <span className="small">{f.type}</span>{' '}
                <span className="badge badge-warn">{t('curationFieldMissing')}</span>
              </li>
            ) : (
              <li key={f.name}>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={current[f.name] ?? false}
                    onChange={() => setChecked({ ...current, [f.name]: !current[f.name] })}
                  />
                  <span className="mono">{f.name}</span>
                  <span className="muted small">{f.type}</span>
                  {f.ref ? <span className="badge badge-muted">{t('curationFieldRef')}</span> : null}
                </label>
              </li>
            ),
          )}
        </ul>
      ) : (
        <p className="muted">{t('curationNoFields')}</p>
      )}
      <div className="row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() =>
            onSave(fields.filter((f) => !f.missing && current[f.name]).map((f) => f.name))
          }
          disabled={pending}
        >
          {t('curationSaveFields')}
        </button>
        <button type="button" className="btn" onClick={onClose}>
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
      <div key={scope.name || '(object)'} className="panel stack stack-tight">
        <p className="panel-label">{label}</p>
        <div className="stack stack-tight">
          <input
            className="input"
            value={f.business_name}
            onChange={(e) => update(scope.name, 'business_name', e.target.value)}
            placeholder={t('enrichBusinessName')}
            aria-label={t('enrichBusinessName')}
          />
          <input
            className="input"
            value={f.description}
            onChange={(e) => update(scope.name, 'description', e.target.value)}
            placeholder={t('enrichDescription')}
            aria-label={t('enrichDescription')}
          />
          <input
            className="input"
            value={f.concept}
            onChange={(e) => update(scope.name, 'concept', e.target.value)}
            placeholder={t('enrichConcept')}
            aria-label={t('enrichConcept')}
          />
          <input
            className="input"
            value={f.tags}
            onChange={(e) => update(scope.name, 'tags', e.target.value)}
            placeholder={t('enrichTags')}
            aria-label={t('enrichTags')}
          />
        </div>
        {readonlyKeys.length > 0 ? (
          <p className="muted small">
            {t('enrichReadonly')}: {readonlyKeys.join(', ')}
          </p>
        ) : null}
      </div>
    )
  }

  return (
    <div className="panel stack">
      <p className="panel-label">{t('enrichTitle', { title: view.data?.title ?? path })}</p>
      <ErrorList messages={messages} />
      {view.data ? (
        <>
          {editor(view.data.object, t('enrichObjectScope'))}
          {view.data.fields.map((field) => editor(field, field.name))}
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
              {t('enrichSave')}
            </button>
            <button type="button" className="btn" onClick={onClose}>
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

function _reviewBody(data: ReviewResult, checked: Record<string, boolean>) {
  const apply_fields: ReviewFieldRef[] = []
  const take_fields: ReviewFieldRef[] = []
  const remove_fields: ReviewFieldRef[] = []
  const remove_objects: string[] = []
  const retain_objects: string[] = []
  for (const o of data.objects) {
    if (o.status === 'object-disappeared') {
      if (checked[`o:${o.path}`]) remove_objects.push(o.path)
    } else if (o.status === 'new') {
      if (checked[`o:${o.path}`]) retain_objects.push(o.path)
    } else {
      for (const f of o.fields) {
        if (!checked[`f:${o.path}:${f.name}`]) continue
        if (f.change === 'changed') apply_fields.push({ path: o.path, name: f.name })
        else if (f.change === 'appeared') take_fields.push({ path: o.path, name: f.name })
        else if (f.change === 'disappeared') remove_fields.push({ path: o.path, name: f.name })
      }
    }
  }
  return { head_sha: data.head_sha, apply_fields, take_fields, remove_fields, remove_objects, retain_objects }
}

function ReviewTab({
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
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [messages, setMessages] = useState<string[]>([])
  const [historyFor, setHistoryFor] = useState<string | null>(null)

  const review = useQuery({
    queryKey: ['review', spaceId, branchId, slug],
    queryFn: () => api.getReview(spaceId, branchId, slug),
    retry: false,
  })
  const data = review.data

  const apply = useMutation({
    mutationFn: () => api.applyReview(spaceId, branchId, slug, _reviewBody(data as ReviewResult, checked)),
    onSuccess: async () => {
      setChecked({})
      setMessages([])
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['review', spaceId, branchId, slug] }),
        queryClient.invalidateQueries({ queryKey: ['curation', spaceId, branchId, slug] }),
      ])
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const toggle = (key: string) => setChecked((c) => ({ ...c, [key]: !c[key] }))
  const chosen = Object.values(checked).filter(Boolean).length

  if (review.isError) {
    return (
      <div className="panel">
        <ErrorList messages={errorMessages(t, review.error)} />
      </div>
    )
  }
  if (!data) {
    return (
      <div className="panel">
        <p className="muted">{t('loading')}</p>
      </div>
    )
  }

  const fieldLabel = (change: string) =>
    change === 'changed'
      ? t('reviewApplyField')
      : change === 'appeared'
        ? t('reviewTakeField')
        : t('reviewRemoveField')

  return (
    <div className="panel stack">
      <p className="muted small">{t('reviewIntro')}</p>
      <ErrorList messages={messages} />

      {data.up_to_date || data.objects.length === 0 ? (
        <div className="empty-note">{t('reviewNothing')}</div>
      ) : (
        <>
          {data.objects.map((o: ReviewObjectItem) => (
            <div key={o.path} className="panel stack stack-tight">
              <div className="row">
                <strong>{o.title}</strong>
                {o.status === 'new' ? (
                  <span className="badge badge-warn">{t('reviewStatusNew')}</span>
                ) : null}
                {o.status === 'object-disappeared' ? (
                  <span className="badge badge-warn">{t('reviewStatusGone')}</span>
                ) : null}
                <span className="mono muted small">{o.path}</span>
                <button
                  type="button"
                  className="btn btn-sm push-right"
                  onClick={() => setHistoryFor(historyFor === o.path ? null : o.path)}
                >
                  {t('reviewHistory')}
                </button>
              </div>

              {o.status === 'new' ? (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={!!checked[`o:${o.path}`]}
                    onChange={() => toggle(`o:${o.path}`)}
                  />
                  {t('reviewRetainObject')}
                </label>
              ) : null}

              {o.status === 'object-disappeared' ? (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={!!checked[`o:${o.path}`]}
                    onChange={() => toggle(`o:${o.path}`)}
                  />
                  {t('reviewRemoveObject')}
                </label>
              ) : null}

              {o.status === 'retained-touched' ? (
                <ul className="list-plain stack stack-tight">
                  {o.fields.map((f) => (
                    <li key={f.name}>
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={!!checked[`f:${o.path}:${f.name}`]}
                          onChange={() => toggle(`f:${o.path}:${f.name}`)}
                        />
                        <span className="badge badge-muted">{t(`reviewChange_${f.change}`)}</span>
                        <span className="mono">{f.name}</span>
                        <span className="muted small">{f.type}</span>
                        <em className="small">→ {fieldLabel(f.change)}</em>
                      </label>
                      {f.lost_enrichment.length > 0 ? (
                        <div className="muted small" style={{ marginLeft: '1.4rem' }}>
                          {t('reviewLostEnrichment', { keys: f.lost_enrichment.join(', ') })}
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}

              {historyFor === o.path ? (
                <HistoryPanel spaceId={spaceId} branchId={branchId} slug={slug} path={o.path} />
              ) : null}
            </div>
          ))}

          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                if (window.confirm(t('reviewConfirm', { count: chosen }))) apply.mutate()
              }}
              disabled={apply.isPending}
            >
              {t('reviewValidate')}
            </button>
            <button type="button" className="btn" onClick={() => setChecked({})} disabled={chosen === 0}>
              {t('reviewAbandon')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function RulesPanel({
  spaceId,
  branchId,
  slug,
  onSaved,
}: {
  spaceId: string
  branchId: string
  slug: string
  onSaved: () => Promise<unknown>
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [rules, setRules] = useState<TriRule[] | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const config = useQuery({
    queryKey: ['curationRules', spaceId, branchId, slug],
    queryFn: () => api.getCurationRules(spaceId, branchId, slug),
    retry: false,
  })
  const targets = config.data?.targets ?? []
  const kinds = config.data?.kinds ?? []
  const current: TriRule[] = rules ?? config.data?.rules ?? []

  const save = useMutation({
    mutationFn: () => api.setCurationRules(spaceId, branchId, slug, current),
    onSuccess: async () => {
      setMessages([])
      setNotice(t('curationRulesSaved'))
      setRules(null)
      await queryClient.invalidateQueries({
        queryKey: ['curationRules', spaceId, branchId, slug],
      })
      await onSaved()
    },
    onError: (err) => {
      setNotice('')
      setMessages(errorMessages(t, err))
    },
  })

  const update = (i: number, patch: Partial<TriRule>) =>
    setRules(current.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const addRule = () =>
    setRules([
      ...current,
      { target: targets[0] ?? 'object-name', pattern: '', kind: kinds[0] ?? 'glob', label: '' },
    ])
  const removeRule = (i: number) => setRules(current.filter((_, j) => j !== i))

  return (
    <div className="panel stack">
      <p className="panel-label">{t('curationRulesTitle')}</p>
      <p className="muted small">{t('curationRulesIntro')}</p>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}
      {config.data ? (
        <>
          {current.length === 0 ? <p className="muted">{t('curationRulesEmpty')}</p> : null}
          {current.map((r, i) => (
            <div key={i} className="toolbar">
              <select
                className="select"
                value={r.target}
                aria-label={t('curationRuleTarget')}
                onChange={(e) => update(i, { target: e.target.value })}
              >
                {targets.map((tg) => (
                  <option key={tg} value={tg}>
                    {t(`triTarget_${tg}`)}
                  </option>
                ))}
              </select>
              <select
                className="select"
                value={r.kind}
                aria-label={t('curationRuleKind')}
                onChange={(e) => update(i, { kind: e.target.value })}
              >
                {kinds.map((k) => (
                  <option key={k} value={k}>
                    {t(`triKind_${k}`)}
                  </option>
                ))}
              </select>
              <input
                className="input"
                value={r.pattern}
                placeholder={t('curationRulePattern')}
                aria-label={t('curationRulePattern')}
                onChange={(e) => update(i, { pattern: e.target.value })}
              />
              <input
                className="input"
                value={r.label ?? ''}
                placeholder={t('curationRuleLabel')}
                aria-label={t('curationRuleLabel')}
                onChange={(e) => update(i, { label: e.target.value })}
              />
              <button type="button" className="btn" onClick={() => removeRule(i)}>
                {t('curationRuleRemove')}
              </button>
            </div>
          ))}
          <div className="row">
            <button type="button" className="btn" onClick={addRule}>
              {t('curationRuleAdd')}
            </button>
            <button type="button" className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
              {t('curationRulesSave')}
            </button>
          </div>
        </>
      ) : config.isError ? (
        <ErrorList messages={errorMessages(t, config.error)} />
      ) : (
        <p className="muted">{t('loading')}</p>
      )}
    </div>
  )
}

function HistoryPanel({
  spaceId,
  branchId,
  slug,
  path,
}: {
  spaceId: string
  branchId: string
  slug: string
  path: string
}) {
  const { t } = useTranslation()
  const history = useQuery({
    queryKey: ['reviewHistory', spaceId, branchId, slug, path],
    queryFn: () => api.getReviewHistory(spaceId, branchId, slug, path),
    retry: false,
  })
  const commits = history.data?.commits ?? []
  return (
    <div className="panel">
      <p className="panel-label">{t('reviewHistoryTitle', { path })}</p>
      {history.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {!history.isLoading && commits.length === 0 ? (
        <p className="muted">{t('reviewHistoryEmpty')}</p>
      ) : null}
      {commits.length > 0 ? (
        <ul className="list-plain stack stack-tight small">
          {commits.map((c) => (
            <li key={c.sha}>
              <span className="mono">{c.sha.slice(0, 8)}</span> · {c.date.slice(0, 10)} ·{' '}
              {c.author} — {c.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
