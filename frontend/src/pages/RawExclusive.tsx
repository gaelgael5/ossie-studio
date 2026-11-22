import { type ReactNode, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AssetButton, AssetEditorProvider, EnrichTab, EyeCell } from './RawAssetEditor'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type {
  RawExplorerMode,
  RawHistoryChange,
  RawObjectDetail,
  RawReference,
  RawTarget,
} from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { SafeMarkdown, sectionsMentioning } from '../components/SafeMarkdown'
import {
  ExposureLegend,
  FieldType,
  TargetLink,
  Technical,
  VisibilityBadge,
} from './RawPreview'

// Vue exclusive d'un objet (double-clic sur une vignette) : tout ce que montre
// l'aperçu, en pleine page et en détail. Lecture seule. La vue a sa propre
// adresse (?open=…) : le bouton retour du navigateur ramène aux vignettes, dans
// le même dossier, même mode, mêmes filtres (l'état de l'explorateur est gardé).

type Tab =
  | 'overview'
  | 'fields'
  | 'enrichment'
  | 'documentation'
  | 'references'
  | 'json'
  | 'technical'
  | 'history'
const TABS: Tab[] = [
  'overview',
  'fields',
  'enrichment',
  'documentation',
  'references',
  'json',
  'technical',
  'history',
]
const STALE_MS = 30_000

interface ExclusiveProps {
  spaceId: string
  branchId: string
  slug: string
  mode: RawExplorerMode
  file: string
  /** Objets du dossier d'origine, dans l'ordre et avec les filtres affichés. */
  siblings: string[]
  folderHref: string
  markdownParser: boolean
  onBack: () => void
  onNavigate: (file: string) => void
  onOpenTarget: (target: RawTarget) => void
  onGraph: () => void
}

export function RawExclusive(props: ExclusiveProps) {
  const { t } = useTranslation()
  const { spaceId, branchId, slug, mode, file, siblings } = props
  const [tab, setTab] = useState<Tab>('overview')
  const detail = useQuery({
    queryKey: ['rawObject', spaceId, branchId, slug, mode, file],
    queryFn: () => api.getRawObject(spaceId, branchId, slug, mode, file),
    staleTime: STALE_MS,
    retry: false,
  })
  const history = useQuery({
    queryKey: ['rawObjectHistory', spaceId, branchId, slug, file],
    queryFn: () => api.getRawObjectHistory(spaceId, branchId, slug, file),
    staleTime: STALE_MS,
    retry: false,
  })
  const docs = useQueries({
    queries: (detail.data?.documents ?? []).map((d) => ({
      queryKey: ['rawDocContent', spaceId, branchId, slug, d.path],
      queryFn: () => api.getRawDocumentContent(spaceId, branchId, slug, d.path),
      staleTime: STALE_MS,
      retry: false,
    })),
  })

  const index = siblings.indexOf(file)
  const prev = index > 0 ? siblings[index - 1] : null
  const next = index >= 0 && index < siblings.length - 1 ? siblings[index + 1] : null

  const nav = (
    <div className="raw-exclusive-nav">
      <button type="button" className="btn btn-sm" onClick={props.onBack}>
        {t('rawBackToTiles')}
      </button>
      <span className="raw-exclusive-step">
        <button
          type="button"
          className="btn btn-sm"
          disabled={!prev}
          onClick={() => prev && props.onNavigate(prev)}
        >
          {t('rawPrevObject')}
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={!next}
          onClick={() => next && props.onNavigate(next)}
        >
          {t('rawNextObject')}
        </button>
      </span>
    </div>
  )

  if (detail.isLoading) {
    return (
      <div className="raw-exclusive">
        {nav}
        <p aria-busy="true">
          <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
        </p>
      </div>
    )
  }
  if (detail.error || !detail.data) {
    const gone =
      detail.error instanceof ApiRequestError &&
      detail.error.body.error === 'raw_explorer_object_not_found'
    return (
      <div className="raw-exclusive">
        {nav}
        {gone ? (
          <div className="empty-note">
            {t('rawObjectGone', { file })} <Link to={props.folderHref}>{t('rawGoFolder')}</Link>
          </div>
        ) : (
          <ErrorList messages={errorMessages(t, detail.error)} />
        )}
      </div>
    )
  }
  const d = detail.data
  const incoming = d.incoming ?? []
  const commits = history.data?.commits ?? []
  const counts: Partial<Record<Tab, number>> = {
    fields: d.fields.length,
    documentation: d.documents.length,
    references: d.references.length + incoming.length,
    ...(history.data ? { history: commits.length } : {}),
  }

  return (
    <AssetEditorProvider spaceId={spaceId} branchId={branchId} slug={slug} detail={d}>
    <div className="raw-exclusive">
      {nav}
      <header className="raw-exclusive-head">
        <div>
          <h2>{d.title}</h2>
          <div className="mono small muted" title={d.qualified_name}>
          {d.namespace ? t('rawNamespaceLine', { namespace: d.namespace }) : d.qualified_name}
        </div>
          <div className="raw-badges">
            <span className="pill">{t(`rawKind.${d.kind}`, { defaultValue: d.kind })}</span>
            <VisibilityBadge visibility={d.visibility} source={d.visibility_source} />
            {d.badges.map((b) => (
              <span key={b} className={`raw-badge raw-badge-${b}`}>
                {t(`rawBadge.${b}`)}
              </span>
            ))}
          </div>
        </div>
        <div className="raw-shortcuts">
          <button type="button" className="btn btn-sm" onClick={props.onGraph}>
            {t('rawViewGraph')}
          </button>
          {d.forge_url ? (
            <a href={d.forge_url} className="btn btn-sm" target="_blank" rel="noreferrer">
              {t('rawOpenInForge')}
            </a>
          ) : null}
          <AssetButton />
        </div>
      </header>

      <div className="tabs" role="tablist">
        {TABS.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={`tab${tab === id ? ' active' : ''}`}
            onClick={() => setTab(id)}
          >
            {t(`rawExclusiveTab.${id}`)}
            {counts[id] !== undefined ? ` (${counts[id]})` : ''}
          </button>
        ))}
      </div>

      {tab === 'overview' ? (
        <div className="raw-exclusive-overview">
          <div className="raw-exclusive-main">
            <section className="panel">
              <h3 className="panel-label">{t('rawDescription')}</h3>
              {d.description ? (
                <>
                  <p>{d.description}</p>
                  <p className="small muted">
                    {t('rawDescriptionProvenance', {
                      file: d.origin.file || '—',
                      line: d.origin.line ?? '—',
                    })}
                  </p>
                </>
              ) : (
                <p className="muted">{t('rawNoDescription')}</p>
              )}
            </section>
            <section className="panel">
              <FieldsTable detail={d} slug={slug} onOpen={props.onOpenTarget} />
            </section>
            <section className="panel">
              <ReferencesGrid
                references={d.references}
                incoming={incoming}
                onOpen={props.onOpenTarget}
              />
            </section>
          </div>
          <aside className="raw-exclusive-side">
            <section className="panel">
              <h3 className="panel-label">
                {t('rawExclusiveTab.documentation')} ({d.documents.length})
              </h3>
              {d.documents.length === 0 ? (
                <p className="muted small">{t('rawNoCitingDocument')}</p>
              ) : (
                <ul className="raw-plain-list">
                  {d.documents.map((doc) => (
                    <li key={doc.path}>
                      <button
                        type="button"
                        className="btn-link"
                        onClick={() => setTab('documentation')}
                      >
                        {doc.title}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="panel">
              <h3 className="panel-label">{t('rawExclusiveTab.technical')}</h3>
              <Technical detail={d} />
            </section>
            <section className="panel">
              <h3 className="panel-label">{t('rawExclusiveTab.history')}</h3>
              <HistoryList commits={commits.slice(0, 3)} loading={history.isLoading} />
            </section>
          </aside>
        </div>
      ) : null}

      {tab === 'fields' ? (
        <section className="panel">
          <FieldsTable detail={d} slug={slug} onOpen={props.onOpenTarget} />
        </section>
      ) : null}

      {tab === 'documentation' ? (
        d.documents.length === 0 ? (
          <div className="empty-note">
            {t('rawNoCitingDocument')}
            {!props.markdownParser ? <p className="small">{t('rawEnableMarkdown')}</p> : null}
          </div>
        ) : (
          d.documents.map((doc, i) => {
            const content = docs[i]?.data
            const sections = content ? sectionsMentioning(content.markdown, d.title) : []
            return (
              <section key={doc.path} className="panel raw-doc">
                <h3>{doc.title}</h3>
                <div className="mono small muted">{doc.path}</div>
                {sections.length > 0 ? (
                  <p className="small muted">
                    {t('rawMentionedIn', { sections: sections.join(' · ') })}
                  </p>
                ) : null}
                {docs[i]?.isLoading ? (
                  <p aria-busy="true">
                    <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
                  </p>
                ) : content ? (
                  <SafeMarkdown markdown={content.markdown} highlightTerm={d.title} />
                ) : (
                  <ErrorList messages={errorMessages(t, docs[i]?.error)} />
                )}
              </section>
            )
          })
        )
      ) : null}

      {tab === 'references' ? (
        <section className="panel">
          <ReferencesGrid
            references={d.references}
            incoming={incoming}
            onOpen={props.onOpenTarget}
          />
        </section>
      ) : null}

      {tab === 'json' ? <pre className="raw-json raw-json-full">{d.json}</pre> : null}
      {tab === 'technical' ? (
        <section className="panel">
          <Technical detail={d} />
        </section>
      ) : null}
      {tab === 'enrichment' ? (
        <section className="panel">
          <EnrichTab spaceId={spaceId} branchId={branchId} slug={slug} file={d.file} />
        </section>
      ) : null}
      {tab === 'history' ? (
        <section className="panel">
          <HistoryList commits={commits} loading={history.isLoading} />
          {history.error ? <ErrorList messages={errorMessages(t, history.error)} /> : null}
        </section>
      ) : null}
    </div>
    </AssetEditorProvider>
  )
}

function FieldsTable({
  detail,
  slug,
  onOpen,
}: {
  detail: RawObjectDetail
  slug: string
  onOpen: (t: RawTarget) => void
}) {
  const { t } = useTranslation()
  return (
    <>
      <h3 className="panel-label">
        {t('rawFieldsTitle')} ({detail.fields.length})
      </h3>
      <table className="table raw-fields">
        <thead>
          <tr>
            <th aria-label={t('rawColExposed')} />
            <th>{t('rawColName')}</th>
            <th>{t('rawColType')}</th>
            <th>{t('rawColVisibility')}</th>
            <th>{t('rawColRequired')}</th>
            <th>{t('rawDescription')}</th>
            <th>{t('rawAnnotations')}</th>
          </tr>
        </thead>
        <tbody>
          {detail.fields.map((f) => (
            <tr key={f.name}>
              <td>
                <EyeCell name={f.name} exposed={f.exposed} />
              </td>
              <td className="mono">{f.name}</td>
              <td>
                <FieldType type={f.type} onOpen={onOpen} />
              </td>
              <td>
                <VisibilityBadge visibility={f.visibility} source={f.visibility_source} />
              </td>
              <td>{f.required ? t('rawRequired') : t('rawOptional')}</td>
              <td className="small">{f.description}</td>
              <td className="mono small">{f.annotations.join(' ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ExposureLegend detail={detail} slug={slug} />
    </>
  )
}

function ReferencesGrid({
  references,
  incoming,
  onOpen,
}: {
  references: RawReference[]
  incoming: { field: string; kind: string; cardinality: string; target: RawTarget }[]
  onOpen: (t: RawTarget) => void
}) {
  const { t } = useTranslation()
  const inherits = references.filter((r) => r.kind === 'inherits')
  const outgoing = references.filter((r) => r.kind !== 'inherits')
  const column = (title: string, rows: ReactNode[]) => (
    <div>
      <h4 className="panel-label">
        {title} ({rows.length})
      </h4>
      {rows.length === 0 ? (
        <p className="muted small">—</p>
      ) : (
        <ul className="raw-plain-list">{rows}</ul>
      )}
    </div>
  )
  return (
    <div className="raw-ref-grid">
      {column(
        t('rawGraphCategory.outgoing'),
        outgoing.map((r, i) => (
          <li key={`o${i}`}>
            <span className="mono">{r.field}</span> →{' '}
            {r.external ? (
              <span className="mono">
                {r.label} <span className="raw-badge">{t('rawExternal')}</span>
              </span>
            ) : (
              <TargetLink target={r.target} label={r.label} onOpen={onOpen} />
            )}{' '}
            <span className="mono small muted">{r.cardinality}</span>
          </li>
        )),
      )}
      {column(
        t('rawGraphCategory.incoming'),
        incoming.map((r, i) => (
          <li key={`i${i}`}>
            <TargetLink target={r.target} label={r.target.title} onOpen={onOpen} />
            <span className="mono"> .{r.field}</span>{' '}
            <span className="mono small muted">{r.cardinality}</span>
          </li>
        )),
      )}
      {column(
        t('rawGraphCategory.inherits'),
        inherits.map((r, i) => (
          <li key={`h${i}`}>
            {r.external ? (
              <span className="mono">
                {r.label} <span className="raw-badge">{t('rawExternal')}</span>
              </span>
            ) : (
              <TargetLink target={r.target} label={r.label} onOpen={onOpen} />
            )}
          </li>
        )),
      )}
    </div>
  )
}

function changeText(
  t: (k: string, o?: Record<string, unknown>) => string,
  c: RawHistoryChange,
): string {
  return t(`rawChange.${c.code}`, {
    field: c.field,
    before: c.before,
    after: c.after,
    state: c.required ? t('rawRequired') : t('rawOptional'),
  })
}

function HistoryList({
  commits,
  loading,
}: {
  commits: {
    sha: string
    author: string
    date: string
    message: string
    changes: RawHistoryChange[]
  }[]
  loading: boolean
}) {
  const { t } = useTranslation()
  if (loading) {
    return (
      <p aria-busy="true">
        <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
      </p>
    )
  }
  if (commits.length === 0) return <p className="muted small">{t('rawNoHistory')}</p>
  return (
    <ul className="raw-plain-list">
      {commits.map((c) => (
        <li key={c.sha}>
          <span className="mono">{c.sha.slice(0, 7)}</span> {c.message}
          <div className="small muted">
            {c.author} · {c.date}
          </div>
          <ul className="raw-changes">
            {c.changes.map((ch, i) => (
              <li key={i}>{changeText(t, ch)}</li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  )
}

/** Un document en pleine page (double-clic sur une vignette de document). */
export function RawDocumentPage({
  spaceId,
  branchId,
  slug,
  path,
  onBack,
}: {
  spaceId: string
  branchId: string
  slug: string
  path: string
  onBack: () => void
}) {
  const { t } = useTranslation()
  const doc = useQuery({
    queryKey: ['rawDocContent', spaceId, branchId, slug, path],
    queryFn: () => api.getRawDocumentContent(spaceId, branchId, slug, path),
    staleTime: STALE_MS,
    retry: false,
  })
  return (
    <div className="raw-exclusive">
      <div className="raw-exclusive-nav">
        <button type="button" className="btn btn-sm" onClick={onBack}>
          {t('rawBackToTiles')}
        </button>
      </div>
      {doc.isLoading ? (
        <p aria-busy="true">
          <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
        </p>
      ) : doc.data ? (
        <section className="panel raw-doc">
          <h2>{doc.data.title}</h2>
          <div className="mono small muted">{doc.data.path}</div>
          <SafeMarkdown markdown={doc.data.markdown} />
        </section>
      ) : (
        <ErrorList messages={errorMessages(t, doc.error)} />
      )}
    </div>
  )
}
