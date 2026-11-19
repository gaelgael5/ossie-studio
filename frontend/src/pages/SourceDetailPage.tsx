import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import type { CollectReport, CollectionRun, SourceItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useWorkspace } from '../workspace'

type Tab = 'overview' | 'collectes' | 'manifeste' | 'raw'

function sourceMeta(s: SourceItem): string {
  const p = s.parametres ?? {}
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
  const [tab, setTab] = useState<Tab>('collectes')
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
          {source?.ok ? <p className="page-sub mono">{sourceMeta(source)}</p> : null}
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
            disabled={collect.isPending || source?.type !== 'git'}
            onClick={() => collect.mutate()}
          >
            {t('collect')}
          </button>
        </div>
      </div>

      <div className="tabs">
        {(['overview', 'collectes', 'manifeste', 'raw'] as Tab[]).map((id) => (
          <button
            key={id}
            type="button"
            className={`tab${tab === id ? ' active' : ''}`}
            aria-disabled={id === 'raw'}
            onClick={() => (id === 'raw' ? undefined : setTab(id))}
          >
            {t(
              id === 'overview'
                ? 'sourceOverview'
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

      {tab === 'overview' ? (
        <div className="panel">
          <p className="panel-label">{t('sourceOverview')}</p>
          {source?.ok ? <p className="mono">{sourceMeta(source)}</p> : <p className="muted">{t('sourceInError')}</p>}
        </div>
      ) : null}

      {tab === 'raw' ? <div className="empty-note">{t('rawExplorerSoon')}</div> : null}

      {tab === 'manifeste' ? (
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

      {tab === 'collectes' ? (
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
