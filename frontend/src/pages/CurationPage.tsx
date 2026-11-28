import { Suspense, lazy, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type {
  ClassificationRemoval,
  AssetGovernance,
  ClassScheme,
  CurationAssetDetail,
  CurationAssetProperty,
  CurationAssetSummary,
} from '../apiClient'
import { ClassBadges } from '../components/ClassBadges'
import { REMOVAL_REFRESH, useCanRemoveClasses } from '../components/FieldRuleClasses'
import { AddToDomainDialog } from '../components/AddToDomainDialog'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import type { MarkdownEditorHandle } from '../components/MarkdownEditor'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import { LinkReferentielDialog, ReferentielLinkCell, useLinkTargets } from '../curation/ReferentielLink'
import { IconButton } from '../components/IconButton'

// Page « Curation » (ticket docflow aeb2e429) : les assets RETENUS de toutes les
// sources, pour les consulter et rédiger leurs descriptions. Rien à voir avec
// l'ancien onglet « Curation » d'une source (liste de tous les objets raw,
// supprimé) : on retient depuis l'Explorateur raw. Gael, 22/11/2026 : « dans
// curation on a que les objets que l'on a retenus ».
// L'écriture passe par l'enrichissement existant (`x-description`), en
// n'envoyant que la description (le nom métier et les tags restent).

// L'éditeur (BlockNote + Mantine) pèse lourd : chargé à l'ouverture de la fenêtre.
const MarkdownEditor = lazy(() => import('../components/MarkdownEditor'))

// « orphan » : l'objet raw de l'asset a disparu (ticket ce21a9e8, Gael : « si je
// supprime les raws, je dois voir les orphelins sur les assets »).
type Filter = 'all' | 'undescribed' | 'modified' | 'orphan'
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
  if (filter === 'orphan') return asset.orphan
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
  const schemes = listing.data?.schemes ?? []
  const labels = new Map((listing.data?.sources ?? []).map((s) => [s.slug, s.label]))
  // Filtre par niveau de classification (`schéma/niveau`) : un asset apparaît si
  // l'un de ses niveaux effectifs, champs compris, correspond (ticket 5e8680d7).
  const level = params.get('level') ?? ''
  // Domaines (ticket 36d35a88) : appartenance de chaque asset, filtre, et
  // sélection pour « Ajouter à un domaine… ».
  const domainFilter = params.get('domain') ?? ''
  const domainList = useQuery({
    queryKey: ['domains', spaceId, branchId],
    queryFn: () => api.listDomains(spaceId, branchId),
  })
  const domainLabels = useMemo(
    () => new Map((domainList.data?.domains ?? []).map((d) => [d.name, d.label])),
    [domainList.data],
  )
  const memberOf = useMemo(() => {
    const out = new Map<string, string[]>()
    for (const d of domainList.data?.domains ?? []) {
      for (const a of d.assets) {
        const key = `${a.source}/${a.path}`
        out.set(key, [...(out.get(key) ?? []), d.name])
      }
    }
    return out
  }, [domainList.data])
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('curation:write')
  // Assets qu'un domaine référence mais qui ne sont plus retenus (bug 206d434c,
  // Gael : « on ne voit plus les assets, même quand je sélectionne un domaine »).
  // Cause rencontrée : la source supprimée puis redéclarée efface ses assets, et
  // le domaine pointe toujours vers eux. Sans ce signalement, la liste est vide
  // sans explication. Seulement le dire : ni reconstruction ni garde (choix de Gael).
  const missing = useMemo(() => {
    if (!listing.data) return []
    const present = new Set(assets.map((a) => `${a.source}/${a.path}`))
    return (domainList.data?.domains ?? [])
      .map((d) => ({
        name: d.name,
        label: d.label,
        assets: d.assets.filter((a) => !present.has(`${a.source}/${a.path}`)),
      }))
      .filter((d) => d.assets.length > 0 && (!domainFilter || d.name === domainFilter))
  }, [listing.data, assets, domainList.data, domainFilter])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [grouping, setGrouping] = useState(false)

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
      (!level || a.class_levels.includes(level)) &&
      (!domainFilter || (memberOf.get(`${a.source}/${a.path}`) ?? []).includes(domainFilter)) &&
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
    orphan: assets.filter((a) => a.orphan).length,
  }
  const current = assets.find((a) => a.source === source && a.path === path)
  const toggleSelected = (key: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  const selectedRefs = [...selected].map((key) => {
    const at = key.indexOf('/')
    return { source: key.slice(0, at), path: key.slice(at + 1) }
  })

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
          {(['all', 'undescribed', 'modified', 'orphan'] as Filter[])
            // « Orphelins » seulement s'il y en a : le cas est rare.
            .filter((f) => f !== 'orphan' || counts.orphan > 0)
            .map((f) => (
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
        {schemes.length > 0 ? (
          <select
            className="select"
            aria-label={t('classFilterLabel')}
            value={level}
            onChange={(e) => update({ level: e.target.value || null })}
          >
            <option value="">{t('classFilterAll')}</option>
            {schemes.map((sc) => (
              <optgroup key={sc.id} label={sc.name}>
                {sc.levels.map((lv) => (
                  <option key={lv.name} value={`${sc.id}/${lv.name}`}>
                    {lv.label || lv.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        ) : null}
        {(domainList.data?.domains ?? []).length > 0 ? (
          <select
            className="select"
            aria-label={t('domainFilterLabel')}
            value={domainFilter}
            onChange={(e) => update({ domain: e.target.value || null })}
          >
            <option value="">{t('domainFilterAll')}</option>
            {(domainList.data?.domains ?? []).map((d) => (
              <option key={d.name} value={d.name}>
                {d.label}
              </option>
            ))}
          </select>
        ) : null}
        {selected.size > 0 ? (
          <div className="banner row" role="status">
            <span>{t('domainSelected', { count: selected.size })}</span>
            <button type="button" className="btn btn-sm btn-primary" onClick={() => setGrouping(true)}>
              {t('domainAddSelected')}
            </button>
          </div>
        ) : null}
        {grouping ? (
          <AddToDomainDialog
            spaceId={spaceId}
            branchId={branchId}
            assets={selectedRefs}
            onClose={() => {
              setGrouping(false)
              setSelected(new Set())
            }}
          />
        ) : null}
        <ErrorList messages={listing.error ? errorMessages(t, listing.error) : []} />
        {missing.map((d) => (
          <div key={d.name} className="banner banner-warn stack stack-tight" role="status">
            <strong>
              {d.label} : {t('curationDomainMissing', { count: d.assets.length })}
            </strong>
            <span className="small">{t('curationDomainMissingHint')}</span>
            {/* Comment résoudre (ticket 7787976d, Gael : « il faut que l'on puisse
                comprendre comment résoudre le problème ») : deux gestes par asset,
                toujours sans reconstruction ni blocage (choix de bug 206d434c). */}
            <span className="small">{t('curationDomainMissingFix')}</span>
            <ul className="list-plain small stack stack-tight">
              {d.assets.map((a) => (
                <li key={`${a.source}/${a.path}`} className="row">
                  <span className="mono">{`${a.source} / ${a.path}`}</span>
                  <Link
                    className="btn btn-sm"
                    to={`/sources?${new URLSearchParams({ tab: 'raw', source: a.source, sel: a.path }).toString()}`}
                  >
                    {t('curationDomainMissingRetain')}
                  </Link>
                  <Link className="btn btn-sm" to={`/domaines/${encodeURIComponent(d.name)}`}>
                    {t('curationDomainMissingRemove')}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
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
                  <li key={a.path} className="curation-row">
                    {canWrite ? (
                      <input
                        type="checkbox"
                        aria-label={t('domainSelectAsset', { name: a.title })}
                        checked={selected.has(`${a.source}/${a.path}`)}
                        onChange={() => toggleSelected(`${a.source}/${a.path}`)}
                      />
                    ) : null}
                    <button
                      type="button"
                      className={`curation-item${a === current ? ' active' : ''}`}
                      aria-current={a === current ? 'true' : undefined}
                      onClick={() => update({ source: a.source, path: a.path, tab: null })}
                    >
                      <span className="curation-glyph" aria-hidden="true">
                        {glyph(a)}
                      </span>
                      <span className="curation-item-title">{a.title}</span>
                      {a.orphan ? (
                        <span className="badge badge-warn" title={t('curationOrphan')}>
                          {t('curationOrphanBadge')}
                        </span>
                      ) : null}
                      {a.classification.slice(0, 1).map((b) => (
                        <ClassBadges
                          key={b.scheme}
                          scheme={schemes.find((sc) => sc.id === b.scheme)}
                          values={b.values}
                          declared={b.declared}
                          extra={b.extra}
                        />
                      ))}
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
        {current ? (
          <AssetPanel
            key={`${current.source}/${current.path}`}
            spaceId={spaceId}
            branchId={branchId}
            summary={current}
            sourceLabel={labels.get(current.source) ?? current.source}
            domains={(memberOf.get(`${current.source}/${current.path}`) ?? []).map((d) => ({
              name: d,
              label: domainLabels.get(d) ?? d,
            }))}
            tab={(params.get('tab') as Tab | null) ?? 'properties'}
            onTab={(tab) => update({ tab: tab === 'properties' ? null : tab })}
            onOpen={(s, p) => update({ source: s, path: p, tab: null })}
            schemes={schemes}
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
  schemes,
  domains,
}: {
  spaceId: string
  branchId: string
  summary: CurationAssetSummary
  sourceLabel: string
  tab: Tab
  onTab: (tab: Tab) => void
  onOpen: (source: string, path: string) => void
  schemes: ClassScheme[]
  domains: { name: string; label: string }[]
}) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('curation:write')
  const [grouping, setGrouping] = useState(false)
  const detail = useQuery({
    queryKey: ['curationAsset', spaceId, branchId, summary.source, summary.path],
    queryFn: () => api.getCurationAsset(spaceId, branchId, summary.source, summary.path),
  })
  // Fenêtre de rédaction : `null` = la description de l'objet, sinon un champ.
  const [writing, setWriting] = useState<{ field: string | null; label: string } | null>(null)
  // Popup « Lier à un référentiel » (ticket 8269c642) : la propriété à lier.
  const [linking, setLinking] = useState<string | null>(null)
  // Liens en lecture seule sur la branche racine du dépôt.
  const linkTargets = useLinkTargets(spaceId, branchId)
  const canLink = canWrite && linkTargets.data?.readonly === false
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
          <div className="row">
            <h1 className="page-title">{data?.business_name || summary.title}</h1>
            {summary.classification.map((b) => (
              <ClassBadges
                key={b.scheme}
                scheme={schemes.find((sc) => sc.id === b.scheme)}
                values={b.values}
                declared={b.declared}
                extra={b.extra}
              />
            ))}
          </div>
          <p className="muted small">
            {t('curationAssetMeta', {
              dir: `sources/${summary.source}/assets/`,
              origin: summary.origin || summary.path,
            })}
            {' · '}
            {t('curationAssetCount', { count: summary.property_count, raw: summary.raw_fields })}
            {summary.orphan ? ` · ${t('curationOrphan')}` : ''}
            {summary.modified ? ` · ${t('curationModified')}` : ''}
            {summary.referentiel_issues ? (
              <span className="text-warn"> · {t('linkIssues', { count: summary.referentiel_issues })}</span>
            ) : null}
          </p>
          {domains.length > 0 ? (
            <p className="small">
              {t('domainMemberOf')}{' '}
              {domains.map((d, i) => (
                <span key={d.name}>
                  {i > 0 ? ', ' : ''}
                  <Link to={`/domaines/${encodeURIComponent(d.name)}`}>{d.label}</Link>
                </span>
              ))}
            </p>
          ) : null}
        </div>
        {canWrite ? (
          <button type="button" className="btn" onClick={() => setGrouping(true)}>
            {t('domainAddWithRefs')}
          </button>
        ) : null}
        {summary.orphan ? null : (
          <Link className="btn" to={explorerHref}>
            {t('curationSeeInExplorer')}
          </Link>
        )}
        {grouping ? (
          <AddToDomainDialog
            spaceId={spaceId}
            branchId={branchId}
            assets={[{ source: summary.source, path: summary.path }]}
            withReferences
            onClose={() => setGrouping(false)}
          />
        ) : null}
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
          linkCell={(p) => (
            <ReferentielLinkCell
              spaceId={spaceId}
              branchId={branchId}
              source={summary.source}
              path={summary.path}
              property={p}
              canWrite={canLink}
              onLink={(prop) => setLinking(prop.name)}
            />
          )}
        />
      ) : null}
      {data && tab === 'description' ? (
        <DescriptionTab
          detail={data}
          canWrite={canWrite}
          onWrite={() => setWriting({ field: null, label: data.business_name || data.title })}
        />
      ) : null}
      {data && tab === 'governance' ? (
        <GovernanceTab
          spaceId={spaceId}
          branchId={branchId}
          summary={summary}
          detail={data}
          canWrite={canWrite}
          explorerHref={explorerHref}
        />
      ) : null}
      {tab === 'history' ? (
        <HistoryTab spaceId={spaceId} branchId={branchId} summary={summary} />
      ) : null}
      {linking && data ? (
        <LinkReferentielDialog
          spaceId={spaceId}
          branchId={branchId}
          source={summary.source}
          path={summary.path}
          property={data.properties.find((p) => p.name === linking)!}
          properties={data.properties}
          onClose={() => setLinking(null)}
        />
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
  linkCell,
}: {
  properties: CurationAssetProperty[]
  canWrite: boolean
  onWrite: (p: CurationAssetProperty) => void
  onOpen: (source: string, path: string) => void
  /** Colonne « Référentiel » (ticket 8269c642). */
  linkCell: (p: CurationAssetProperty) => ReactNode
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
            <th>{t('curationColReferentiel')}</th>
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
                ) : p.source_description ? (
                  // Description de la source (commentaire SQL…) : valeur par
                  // défaut tant que rien n'est rédigé (f578d601).
                  <>
                    {excerpt(p.source_description)}{' '}
                    <span className="badge" title={t('curationFromSourceHint')}>
                      {t('curationFromSource')}
                    </span>
                  </>
                ) : (
                  <span className="curation-todo">{t('curationToDescribeOne')}</span>
                )}
              </td>
              <td>{linkCell(p)}</td>
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
      {detail.description || detail.source_description ? (
        <>
          {!detail.description ? (
            <span className="badge" title={t('curationFromSourceHint')}>
              {t('curationFromSource')}
            </span>
          ) : null}
          <Suspense fallback={<p className="muted">{t('loading')}</p>}>
            <MarkdownEditor
              key={detail.description || detail.source_description}
              initial={detail.description || detail.source_description || ''}
              editable={false}
              label={t('curationTab_description')}
              onReady={() => undefined}
            />
          </Suspense>
        </>
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
  detail,
  canWrite,
  explorerHref,
}: {
  spaceId: string
  branchId: string
  summary: CurationAssetSummary
  detail: CurationAssetDetail
  canWrite: boolean
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
    <div className="curation-gov">
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
      <ClassificationPanel
        spaceId={spaceId}
        branchId={branchId}
        summary={summary}
        governance={detail.governance}
        canWrite={canWrite}
      />
    </div>
  )
}

/** Choix de niveaux d'un schéma : sélecteur (valeur unique) ou cases (multi). */
function LevelPicker({
  scheme,
  value,
  emptyLabel,
  disabled,
  label,
  onChange,
}: {
  scheme: ClassScheme
  value: string[]
  emptyLabel: string
  disabled: boolean
  label: string
  onChange: (names: string[]) => void
}) {
  if (scheme.multi_value) {
    return (
      <div className="stack stack-tight" role="group" aria-label={label}>
        {scheme.levels.map((lv) => (
          <label key={lv.name} className="row small">
            <input
              type="checkbox"
              disabled={disabled}
              checked={value.includes(lv.name)}
              onChange={() =>
                onChange(
                  value.includes(lv.name) ? value.filter((v) => v !== lv.name) : [...value, lv.name],
                )
              }
            />
            {lv.label || lv.name}
          </label>
        ))}
      </div>
    )
  }
  return (
    <select
      className="select"
      aria-label={label}
      disabled={disabled}
      value={value[0] ?? ''}
      onChange={(e) => onChange(e.target.value ? [e.target.value] : [])}
    >
      <option value="">{emptyLabel}</option>
      {scheme.levels.map((lv) => (
        <option key={lv.name} value={lv.name}>
          {lv.label || lv.name}
        </option>
      ))}
    </select>
  )
}

// Classification de l'asset et de ses champs (ticket docflow 5e8680d7, maquette
// « Curation — gouvernance d'un asset »). Chaque choix est enregistré tout de
// suite (un commit dans le brouillon), dans le `.extend.yaml` de l'asset.
function ClassificationPanel({
  spaceId,
  branchId,
  summary,
  governance,
  canWrite,
}: {
  spaceId: string
  branchId: string
  summary: CurationAssetSummary
  governance: AssetGovernance
  canWrite: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [editing, setEditing] = useState<string | null>(null) // « champ/schéma »
  const schemes = governance.schemes
  const save = useMutation({
    mutationFn: (body: { obj?: Record<string, string[]>; fields?: Record<string, Record<string, string[]>> }) =>
      api.classifyAsset(spaceId, branchId, summary.source, { path: summary.path, ...body }),
    onSuccess: async () => {
      setMessages([])
      setEditing(null)
      await Promise.all(
        ['curationAsset', 'curationAssets', 'draft', 'draftChanges'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      )
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  // Retirer une valeur posée par règle (ticket 77ce0d79) : mémorisé dans la
  // gouvernance de l'espace, comme les retraits de tags (droit gouvernance).
  const canRemoveRule = useCanRemoveClasses()
  const removeRule = useMutation({
    mutationFn: (body: ClassificationRemoval) => api.removeFieldClass(body),
    onSuccess: async () => {
      setMessages([])
      await Promise.all(REMOVAL_REFRESH.map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const { source, path, title } = summary
  if (schemes.length === 0) {
    return (
      <div className="panel stack">
        <h3>{t('classAssetTitle')}</h3>
        <p className="muted">
          {t('classNoScheme')} <Link to="/governance/classifications">{t('classManageSchemes')}</Link>
        </p>
      </div>
    )
  }
  const byScheme = new Map(schemes.map((sc) => [sc.id, sc]))
  const gaps = governance.object.filter((v) => v.gap)
  return (
    <div className="panel stack">
      <h3>{t('classAssetTitle')}</h3>
      <ErrorList messages={messages} />
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t('classColScheme')}</th>
              <th>{t('classColEffective')}</th>
              <th>{t('classColDeclare')}</th>
            </tr>
          </thead>
          <tbody>
            {governance.object.map((v) => {
              const scheme = byScheme.get(v.scheme)!
              return (
                <tr key={v.scheme}>
                  <td>{scheme.name}</td>
                  <td>
                    {v.invalid ? (
                      <span className="text-danger small">
                        {t('classInvalid', { value: v.declared_value.join(', ') })}
                      </span>
                    ) : v.values.length === 0 ? (
                      <span className="muted">{t('classNone')}</span>
                    ) : (
                      <div className="stack stack-tight">
                        <ClassBadges scheme={scheme} values={v.values} declared={v.declared} extra={v.extra} />
                        <span className="muted small">
                          {v.declared
                            ? t('classDeclared')
                            : t('classDerivedFrom', { levels: v.derived_from.join(', ') })}
                        </span>
                      </div>
                    )}
                  </td>
                  <td>
                    <LevelPicker
                      scheme={scheme}
                      value={v.declared_value}
                      emptyLabel={t('classDerivedOption')}
                      disabled={!canWrite || save.isPending}
                      label={t('classDeclareFor', { scheme: scheme.name })}
                      onChange={(names) => save.mutate({ obj: { [scheme.id]: names } })}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {gaps.map((v) => (
        <p key={v.scheme} className="class-gap small" role="note">
          {t('classGap', {
            scheme: byScheme.get(v.scheme)?.name ?? v.scheme,
            declared: v.values[0],
            field: v.gap!.field,
            level: v.gap!.level,
          })}
        </p>
      ))}
      <h3>{t('classFieldsTitle')}</h3>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t('classColField')}</th>
              {schemes.map((sc) => (
                <th key={sc.id}>{sc.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {governance.fields.map((f) => (
              <tr key={f.name}>
                <td className="mono">{f.name}</td>
                {schemes.map((sc) => {
                  const values = f.values[sc.id] ?? []
                  const key = `${f.name}/${sc.id}`
                  if (editing === key) {
                    return (
                      <td key={sc.id}>
                        <LevelPicker
                          scheme={sc}
                          value={values}
                          emptyLabel={t('classUnset')}
                          disabled={save.isPending}
                          label={t('classDeclareField', { field: f.name, scheme: sc.name })}
                          onChange={(names) => save.mutate({ fields: { [f.name]: { [sc.id]: names } } })}
                        />
                      </td>
                    )
                  }
                  // Valeur posée par une règle (ticket 568a0547) : badge non déclaré,
                  // marqué « par règle » ; un clic propose d'en déclarer une autre.
                  const rule = f.rules?.[sc.id]
                  return (
                    <td key={sc.id}>
                      {values.length > 0 && rule ? (
                        <span className="row small" title={t('classRuleByRule', { rule: rule.rule_name })}>
                          <ClassBadges scheme={sc} values={values} declared={false} />
                          <span className="muted">{t('classByRule')}</span>
                          {canWrite ? (
                            <button
                              type="button"
                              className="btn-link"
                              aria-label={t('classDeclareOther', { field: f.name, scheme: sc.name })}
                              onClick={() => setEditing(key)}
                            >
                              {t('classDeclareOtherShort')}
                            </button>
                          ) : null}
                          {canRemoveRule ? (
                            <button
                              type="button"
                              className="btn-link"
                              disabled={removeRule.isPending}
                              aria-label={t('classRemoveRuleValue', { field: f.name, scheme: sc.name })}
                              onClick={() =>
                                removeRule.mutate({
                                  object: `${source}/${path}`,
                                  field: f.name,
                                  scheme: sc.id,
                                  rule: rule.rule,
                                  title,
                                })
                              }
                            >
                              {t('classRemoveShort')}
                            </button>
                          ) : null}
                        </span>
                      ) : values.length > 0 ? (
                        <button
                          type="button"
                          className="btn-link"
                          disabled={!canWrite}
                          aria-label={t('classDeclareField', { field: f.name, scheme: sc.name })}
                          onClick={() => setEditing(key)}
                        >
                          <ClassBadges scheme={sc} values={values} declared />
                        </button>
                      ) : canWrite ? (
                        <button
                          type="button"
                          className="btn-link"
                          aria-label={t('classDeclareField', { field: f.name, scheme: sc.name })}
                          onClick={() => setEditing(key)}
                        >
                          {t('classAdd')}
                        </button>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">{t('classLegend')}</p>
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
  const sourceText =
    field === null
      ? detail.source_description ?? ''
      : (detail.properties.find((p) => p.name === field)?.source_description ?? '')
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
      <div role="dialog" aria-modal="true" aria-label={title} className="panel modal-panel modal-editor">
        <div className="row modal-head">
          <h2>{title}</h2>
          <IconButton icon="close" label={t('close')} onClick={onClose} />
        </div>
        <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
        {sourceText ? (
          // La description de la source reste visible pendant la rédaction :
          // ce qu'on écrit la remplacera à l'affichage, sans l'effacer.
          <p className="info-note small">
            <strong>{t('curationFromSourceLabel')}</strong> {sourceText}
          </p>
        ) : null}
        <Suspense fallback={<p className="muted">{t('loading')}</p>}>
          <MarkdownEditor
            initial={initial}
            label={title}
            fill
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
