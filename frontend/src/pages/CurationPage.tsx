import { Suspense, lazy, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type { CurationAssetDetail, CurationAssetProperty, CurationAssetSummary } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import type { MarkdownEditorHandle } from '../components/MarkdownEditor'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'

// Page « Curation » (ticket docflow aeb2e429) : les assets RETENUS de toutes les
// sources, pour les consulter et rédiger leurs descriptions. Rien à voir avec
// l'ancien onglet « Curation » d'une source (liste de tous les objets raw,
// supprimé) : on retient depuis l'Explorateur raw. Gael, 22/11/2026 : « dans
// curation on a que les objets que l'on a retenus ».
// L'écriture passe par l'enrichissement existant (`x-description`), en
// n'envoyant que la description (le nom métier et les tags restent).

// L'éditeur (BlockNote + Mantine) pèse lourd : chargé à l'ouverture de la fenêtre.
const MarkdownEditor = lazy(() => import('../components/MarkdownEditor'))

type Filter = 'all' | 'undescribed' | 'modified'
type Tab = 'properties' | 'description' | 'governance' | 'history'

// Nombre d'assets montrés par source avant « + N autres ».
const FOLD = 8

const PARSER_GLYPH: Record<string, string> = {
  csharp: 'C#',
  java: 'J',
  kotlin: 'K',
  typescript: 'TS',
  python: 'Py',
  sql: 'T',
}

function glyph(asset: CurationAssetSummary): string {
  if (asset.kind === 'enum') return 'E'
  return PARSER_GLYPH[asset.parser] ?? (asset.parser ? asset.parser.slice(0, 2).toUpperCase() : '·')
}

function hasMermaid(text: string): boolean {
  return /```\s*mermaid/i.test(text)
}

/** Début lisible d'une description Markdown (sans blocs de code ni balisage de titre). */
function excerpt(text: string, max = 140): string {
  const lines: string[] = []
  let inFence = false
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (line.startsWith('```')) {
      inFence = !inFence
      continue
    }
    if (inFence || !line) continue
    lines.push(line.replace(/^#+\s*/, '').replace(/^[-*]\s+/, ''))
    if (lines.join(' ').length > max) break
  }
  const joined = lines.join(' ')
  return joined.length > max ? `${joined.slice(0, max).trimEnd()}…` : joined
}

function matches(asset: CurationAssetSummary, filter: Filter): boolean {
  if (filter === 'undescribed') return asset.undescribed > 0
  if (filter === 'modified') return asset.modified
  return true
}

export function CurationPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  if (!ws.spaceId) {
    return (
      <main>
        <div className="empty-note">{t('noSpaceYet')}</div>
      </main>
    )
  }
  if (!ws.branchId) {
    return (
      <main>
        <NoBranchNote spaceId={ws.spaceId} />
      </main>
    )
  }
  return <CurationScreen spaceId={ws.spaceId} branchId={ws.branchId} />
}

function CurationScreen({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const source = params.get('source') ?? ''
  const path = params.get('path') ?? ''
  const filter = (params.get('filter') as Filter | null) ?? 'all'
  const [query, setQuery] = useState('')
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set())

  const listing = useQuery({
    queryKey: ['curationAssets', spaceId, branchId],
    queryFn: () => api.listCurationAssets(spaceId, branchId),
  })
  const assets = listing.data?.assets ?? []
  const labels = new Map((listing.data?.sources ?? []).map((s) => [s.slug, s.label]))

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setParams(next, { replace: true })
  }

  // Recherche sur l'asset (titre, fichier) et sur ses propriétés (noms raw et
  // métier), comme la maquette (« Rechercher un asset, une propriété »).
  const q = query.trim().toLowerCase()
  const visible = assets.filter(
    (a) =>
      matches(a, filter) &&
      (!q ||
        a.title.toLowerCase().includes(q) ||
        a.path.toLowerCase().includes(q) ||
        a.property_names.some((n) => n.toLowerCase().includes(q))),
  )
  const groups = useMemo(() => {
    const out = new Map<string, CurationAssetSummary[]>()
    for (const a of visible) out.set(a.source, [...(out.get(a.source) ?? []), a])
    return [...out.entries()]
  }, [visible])
  const counts = {
    all: assets.length,
    undescribed: assets.filter((a) => a.undescribed > 0).length,
    modified: assets.filter((a) => a.modified).length,
  }
  const selected = assets.find((a) => a.source === source && a.path === path)

  return (
    <main className="curation-page">
      <aside className="curation-side">
        <input
          type="search"
          className="input"
          placeholder={t('curationSearch')}
          aria-label={t('curationSearch')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="curation-filters" role="group" aria-label={t('curationFilters')}>
          {(['all', 'undescribed', 'modified'] as Filter[]).map((f) => (
            <button
              key={f}
              type="button"
              className={`curation-chip${filter === f ? ' active' : ''}`}
              aria-pressed={filter === f}
              onClick={() => update({ filter: f === 'all' ? null : f })}
            >
              {t(`curationFilter_${f}`, { count: counts[f] })}
            </button>
          ))}
        </div>
        <ErrorList messages={listing.error ? errorMessages(t, listing.error) : []} />
        {listing.isLoading ? <p className="muted">{t('loading')}</p> : null}
        {!listing.isLoading && assets.length === 0 ? (
          <div className="empty-note">
            {t('curationEmpty')}{' '}
            <Link to="/sources?tab=raw">{t('curationOpenExplorer')}</Link>
          </div>
        ) : null}
        {assets.length > 0 ? <h3 className="curation-side-title">{t('curationRetained')}</h3> : null}
        {groups.map(([slug, items]) => {
          const open = unfolded.has(slug) || q !== ''
          const shown = open ? items : items.slice(0, FOLD)
          return (
            <section key={slug} className="curation-group">
              <div className="curation-group-head">
                <span className="curation-source">{labels.get(slug) ?? slug}</span>
                <span className="muted">{items.length}</span>
              </div>
              <ul className="curation-list">
                {shown.map((a) => (
                  <li key={a.path}>
                    <button
                      type="button"
                      className={`curation-item${a === selected ? ' active' : ''}`}
                      aria-current={a === selected ? 'true' : undefined}
                      onClick={() => update({ source: a.source, path: a.path, tab: null })}
                    >
                      <span className="curation-glyph" aria-hidden="true">
                        {glyph(a)}
                      </span>
                      <span className="curation-item-title">{a.title}</span>
                      {a.undescribed > 0 ? (
                        <span className="curation-todo">
                          {t('curationToDescribe', { count: a.undescribed })}
                        </span>
                      ) : (
                        <span className="curation-done">{t('curationDescribed')}</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
              {items.length > shown.length ? (
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => setUnfolded((s) => new Set(s).add(slug))}
                >
                  {t('curationMore', { count: items.length - shown.length })}
                </button>
              ) : null}
            </section>
          )
        })}
        <p className="curation-note small muted">{t('curationNote')}</p>
      </aside>
      <section className="curation-main">
        {selected ? (
          <AssetPanel
            key={`${selected.source}/${selected.path}`}
            spaceId={spaceId}
            branchId={branchId}
            summary={selected}
            sourceLabel={labels.get(selected.source) ?? selected.source}
            tab={(params.get('tab') as Tab | null) ?? 'properties'}
            onTab={(tab) => update({ tab: tab === 'properties' ? null : tab })}
            onOpen={(s, p) => update({ source: s, path: p, tab: null })}
          />
        ) : assets.length > 0 ? (
          <p className="muted">{t('curationPick')}</p>
        ) : null}
      </section>
    </main>
  )
}

function AssetPanel({
  spaceId,
  branchId,
  summary,
  sourceLabel,
  tab,
  onTab,
  onOpen,
}: {
  spaceId: string
  branchId: string
  summary: CurationAssetSummary
  sourceLabel: string
  tab: Tab
  onTab: (tab: Tab) => void
  onOpen: (source: string, path: string) => void
}) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('curation:write')
  const detail = useQuery({
    queryKey: ['curationAsset', spaceId, branchId, summary.source, summary.path],
    queryFn: () => api.getCurationAsset(spaceId, branchId, summary.source, summary.path),
  })
  // Fenêtre de rédaction : `null` = la description de l'objet, sinon un champ.
  const [writing, setWriting] = useState<{ field: string | null; label: string } | null>(null)
  const explorerHref = `/sources?${new URLSearchParams({
    tab: 'raw',
    source: summary.source,
    path: summary.folder,
    sel: summary.path,
  }).toString()}`
  const tabs: Tab[] = ['properties', 'description', 'governance', 'history']
  const data = detail.data

  return (
    <div className="stack">
      <div className="breadcrumb">
        {t('navCuration')} › {sourceLabel} › {summary.title}
      </div>
      <div className="curation-head">
        <span className="curation-glyph curation-glyph-lg" aria-hidden="true">
          {glyph(summary)}
        </span>
        <div className="curation-head-text">
          <h1 className="page-title">{data?.business_name || summary.title}</h1>
          <p className="muted small">
            {t('curationAssetMeta', {
              dir: `sources/${summary.source}/assets/`,
              origin: summary.origin || summary.path,
            })}
            {' · '}
            {t('curationAssetCount', { count: summary.property_count, raw: summary.raw_fields })}
            {summary.orphan ? ` · ${t('curationOrphan')}` : ''}
            {summary.modified ? ` · ${t('curationModified')}` : ''}
          </p>
        </div>
        {summary.orphan ? null : (
          <Link className="btn" to={explorerHref}>
            {t('curationSeeInExplorer')}
          </Link>
        )}
      </div>
      <div className="tabs" role="tablist">
        {tabs.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`tab${tab === key ? ' active' : ''}`}
            onClick={() => onTab(key)}
          >
            {t(`curationTab_${key}`)}
          </button>
        ))}
      </div>
      <ErrorList messages={detail.error ? errorMessages(t, detail.error) : []} />
      {detail.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {data && tab === 'properties' ? (
        <PropertiesTable
          properties={data.properties}
          canWrite={canWrite}
          onWrite={(p) => setWriting({ field: p.name, label: p.business_name || p.name })}
          onOpen={onOpen}
        />
      ) : null}
      {data && tab === 'description' ? (
        <DescriptionTab
          detail={data}
          canWrite={canWrite}
          onWrite={() => setWriting({ field: null, label: data.business_name || data.title })}
        />
      ) : null}
      {tab === 'governance' ? (
        <GovernanceTab
          spaceId={spaceId}
          branchId={branchId}
          summary={summary}
          explorerHref={explorerHref}
        />
      ) : null}
      {tab === 'history' ? (
        <HistoryTab spaceId={spaceId} branchId={branchId} summary={summary} />
      ) : null}
      {writing && data ? (
        <RedactionDialog
          spaceId={spaceId}
          branchId={branchId}
          detail={data}
          field={writing.field}
          label={writing.label}
          onClose={() => setWriting(null)}
        />
      ) : null}
    </div>
  )
}

function typeLabel(p: CurationAssetProperty, t: TFunction): string {
  if (p.external) return p.external
  let label = p.format ? p.format : p.type
  if (p.max_length !== null) label = t('curationMaxLength', { type: label, max: p.max_length })
  return label || '—'
}

function PropertiesTable({
  properties,
  canWrite,
  onWrite,
  onOpen,
}: {
  properties: CurationAssetProperty[]
  canWrite: boolean
  onWrite: (p: CurationAssetProperty) => void
  onOpen: (source: string, path: string) => void
}) {
  const { t } = useTranslation()
  if (properties.length === 0) return <p className="muted">{t('curationNoProperty')}</p>
  return (
    <div className="panel table-wrap">
      <table className="table curation-props">
        <thead>
          <tr>
            <th>{t('curationColProperty')}</th>
            <th>{t('curationColType')}</th>
            <th>{t('curationColDescription')}</th>
            <th aria-label={t('curationColActions')} />
          </tr>
        </thead>
        <tbody>
          {properties.map((p) => (
            <tr key={p.name}>
              <td>
                <code>{p.business_name || p.name}</code>
                <div className="small muted">
                  {t('curationRawName', { name: p.name })}
                  {p.missing ? ` · ${t('curationMissing')}` : ''}
                </div>
              </td>
              <td>
                {p.ref ? (
                  p.ref.retained ? (
                    <button
                      type="button"
                      className="btn-link"
                      onClick={() => onOpen(p.ref!.source, p.ref!.path)}
                    >
                      {p.ref.kind === 'field' ? '→ ' : '[] '}
                      {p.ref.title}
                    </button>
                  ) : (
                    <span title={t('curationRefNotRetained')}>
                      {p.ref.kind === 'field' ? '→ ' : '[] '}
                      {p.ref.title}
                    </span>
                  )
                ) : (
                  typeLabel(p, t)
                )}
              </td>
              <td>
                {p.description ? (
                  <>
                    {excerpt(p.description)}
                    {hasMermaid(p.description) ? (
                      <>
                        {' '}
                        <span className="badge">{t('curationMermaid')}</span>
                      </>
                    ) : null}
                  </>
                ) : (
                  <span className="curation-todo">{t('curationToDescribeOne')}</span>
                )}
              </td>
              <td>
                <button
                  type="button"
                  className="btn-link"
                  disabled={!canWrite}
                  title={canWrite ? undefined : t('curationReadonly')}
                  aria-label={t('curationWriteFor', { name: p.business_name || p.name })}
                  onClick={() => onWrite(p)}
                >
                  {t('curationWrite')}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DescriptionTab({
  detail,
  canWrite,
  onWrite,
}: {
  detail: CurationAssetDetail
  canWrite: boolean
  onWrite: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="panel stack">
      {detail.description ? (
        <Suspense fallback={<p className="muted">{t('loading')}</p>}>
          <MarkdownEditor
            key={detail.description}
            initial={detail.description}
            editable={false}
            label={t('curationTab_description')}
            onReady={() => undefined}
          />
        </Suspense>
      ) : (
        <p className="curation-todo">{t('curationToDescribeOne')}</p>
      )}
      <div>
        <button
          type="button"
          className="btn btn-sm"
          disabled={!canWrite}
          title={canWrite ? undefined : t('curationReadonly')}
          onClick={onWrite}
        >
          {t('curationWrite')}
        </button>
      </div>
    </div>
  )
}

function GovernanceTab({
  spaceId,
  branchId,
  summary,
  explorerHref,
}: {
  spaceId: string
  branchId: string
  summary: CurationAssetSummary
  explorerHref: string
}) {
  const { t } = useTranslation()
  const view = useQuery({
    queryKey: ['enrichment', spaceId, branchId, summary.source, summary.path],
    queryFn: () => api.getEnrichment(spaceId, branchId, summary.source, summary.path),
  })
  const fields = (view.data?.fields ?? []).filter((f) => f.tags.length > 0)
  const objectTags = view.data?.object.tags ?? []
  return (
    <div className="panel stack">
      <ErrorList messages={view.error ? errorMessages(t, view.error) : []} />
      <div>
        <h3>{t('curationGovAsset')}</h3>
        {objectTags.length ? (
          <div className="row">
            {objectTags.map((tag) => (
              <span key={tag} className="badge">
                {tag}
              </span>
            ))}
          </div>
        ) : (
          <p className="muted">{t('curationGovNone')}</p>
        )}
      </div>
      <div>
        <h3>{t('curationGovFields')}</h3>
        {fields.length ? (
          <ul className="curation-plain">
            {fields.map((f) => (
              <li key={f.name}>
                <code>{f.name}</code>{' '}
                {f.tags.map((tag) => (
                  <span key={tag} className="badge">
                    {tag}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{t('curationGovNone')}</p>
        )}
      </div>
      <p className="small muted">
        {t('curationGovEdit')} <Link to={`${explorerHref}`}>{t('curationSeeInExplorer')}</Link>
      </p>
    </div>
  )
}

function HistoryTab({
  spaceId,
  branchId,
  summary,
}: {
  spaceId: string
  branchId: string
  summary: CurationAssetSummary
}) {
  const { t } = useTranslation()
  const history = useQuery({
    queryKey: ['curationAssetHistory', spaceId, branchId, summary.source, summary.path],
    queryFn: () => api.getCurationAssetHistory(spaceId, branchId, summary.source, summary.path),
  })
  const commits = history.data?.commits ?? []
  return (
    <div className="panel">
      <ErrorList messages={history.error ? errorMessages(t, history.error) : []} />
      {history.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {!history.isLoading && commits.length === 0 ? (
        <p className="muted">{t('curationHistoryEmpty')}</p>
      ) : null}
      <ul className="curation-plain">
        {commits.map((c) => (
          <li key={c.sha}>
            <code>{c.sha.slice(0, 7)}</code> {c.message}{' '}
            <span className="small muted">
              {t('curationHistoryBy', { author: c.author, date: new Date(c.date).toLocaleString() })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function RedactionDialog({
  spaceId,
  branchId,
  detail,
  field,
  label,
  onClose,
}: {
  spaceId: string
  branchId: string
  detail: CurationAssetDetail
  field: string | null
  label: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const handle = useRef<MarkdownEditorHandle | null>(null)
  const initial =
    field === null
      ? detail.description
      : (detail.properties.find((p) => p.name === field)?.description ?? '')
  const save = useMutation({
    mutationFn: () => {
      const description = handle.current?.getMarkdown() ?? initial
      return api.describeAsset(spaceId, branchId, detail.source, {
        path: detail.path,
        ...(field === null ? { obj: { description } } : { fields: { [field]: { description } } }),
      })
    },
    onSuccess: async () => {
      await Promise.all(
        ['curationAssets', 'curationAsset', 'enrichment', 'rawObject'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      )
      onClose()
    },
  })
  const title =
    field === null
      ? t('curationWriteAssetTitle', { name: label })
      : t('curationWriteFieldTitle', { name: label, asset: detail.business_name || detail.title })
  return (
    <div className="modal-backdrop">
      <div role="dialog" aria-modal="true" aria-label={title} className="panel modal-panel">
        <div className="row modal-head">
          <h2>{title}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
        <Suspense fallback={<p className="muted">{t('loading')}</p>}>
          <MarkdownEditor
            initial={initial}
            label={title}
            onReady={(h) => {
              handle.current = h
            }}
          />
        </Suspense>
        <p className="small muted">{t('curationEditorHint')}</p>
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={save.isPending}
            onClick={() => save.mutate()}
          >
            {t('save')}
          </button>
        </div>
      </div>
    </div>
  )
}
