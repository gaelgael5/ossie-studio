import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import type { CollectReport, CollectionRun, ContractAnalysis, SourceItem } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useWorkspace } from '../workspace'

type Tab = 'overview' | 'contrats' | 'collectes' | 'manifeste' | 'raw'

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
    ? ['overview', 'contrats', 'collectes', 'manifeste', 'raw']
    : ['overview', 'collectes', 'manifeste', 'raw']
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
