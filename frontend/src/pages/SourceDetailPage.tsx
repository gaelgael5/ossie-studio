import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import type { CollectReport, ContractAnalysis, SourceItem } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { BusyButton } from '../components/BusyButton'
import { useSourceCollect } from '../useSourceCollect'
import { CollectChecklist } from '../components/CollectChecklist'
import { CollectErrorCause, splitScanMessage } from '../components/CollectErrorCause'
import { useWorkspace } from '../workspace'
import { exclusionsOf } from '../sourceExclusions'
import { SourceExclusions } from '../components/SourceExclusions'

const LINKABLE_TABS: string[] = ['overview', 'contrats', 'collectes', 'scan', 'manifeste']

type Tab =
  | 'overview'
  | 'contrats'
  | 'collectes'
  | 'scan'
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
  // L'onglet peut venir de l'adresse (?tab=scan…). Les anciens onglets Curation
  // et Revue n'existent plus : la curation se fait dans l'explorateur raw
  // (epic « Curation dans l'explorateur raw ») ; leurs liens y sont redirigés.
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
    ? ['overview', 'contrats', 'collectes', 'scan', 'manifeste', 'raw']
    : ['overview', 'collectes', 'scan', 'manifeste', 'raw']
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

  const legacyTab = searchParams.get('tab')
  if (legacyTab === 'curation' || legacyTab === 'revue') {
    const target = new URLSearchParams({ tab: 'raw', source: slug })
    const object = searchParams.get('object')
    if (legacyTab === 'curation' && object) target.set('sel', object)
    return <Navigate to={`/sources?${target.toString()}`} replace />
  }

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
      {effectiveTab === 'overview' && source?.ok && source.type === 'git' ? (
        <SourceExclusions
          spaceId={ws.spaceId as string}
          branchId={ws.branchId as string}
          source={source}
          onRecollect={() => {
            setTab('collectes')
            collect.start(false)
          }}
        />
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
