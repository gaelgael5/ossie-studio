import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import type {
  RawBadge,
  RawDocumentItem,
  RawExplorerMode,
  RawExplorerSource,
  RawFolderItem,
  RawObjectItem,
} from '../apiClient'
import { errorMessages } from '../errors'
import { ApiRequestError } from '../errors'
import { ErrorList } from '../components/ErrorList'

// Explorateur raw : parcourir la couche raw d'un espace comme un explorateur de
// fichiers, en lecture seule. L'état de navigation (source, mode, dossier, objet
// sélectionné, affichage) vit dans l'adresse : elle se partage, rouvre le même
// dossier, et le bouton retour du navigateur revient au dossier précédent.

const MODES: RawExplorerMode[] = ['origin', 'kind', 'parser']
const BADGES: RawBadge[] = ['retained', 'new', 'modified', 'excluded', 'error']
const MODE_KEY = 'ossie.rawExplorer.mode'
// Chaque lecture réaligne la copie sur la forge côté serveur : on ne relit pas
// un dossier déjà vu à chaque clic.
const STALE_MS = 30_000

type View = 'tiles' | 'list'
type SortKey = 'name' | 'kind' | 'fields' | 'origin' | 'state'

function storedMode(): RawExplorerMode {
  try {
    const value = localStorage.getItem(MODE_KEY)
    return MODES.includes(value as RawExplorerMode) ? (value as RawExplorerMode) : 'origin'
  } catch {
    return 'origin'
  }
}

function storeMode(mode: RawExplorerMode): void {
  try {
    localStorage.setItem(MODE_KEY, mode)
  } catch {
    // stockage indisponible (navigation privée) : le mode reste dans l'adresse
  }
}

function lastSegment(path: string): string {
  return path.includes('/') ? path.slice(path.lastIndexOf('/') + 1) : path
}

function parentOf(path: string): string {
  return path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
}

/** Abréviation en chasse fixe affichée dans l'icône d'une vignette. */
function kindGlyph(kind: string): string {
  switch (kind) {
    case 'class':
      return 'CLS'
    case 'record':
    case 'struct':
      return 'REC'
    case 'enum':
      return 'ENM'
    case 'interface':
      return 'IFC'
    case 'table':
      return 'TBL'
    case 'view':
    case 'materialized-view':
      return 'VUE'
    case 'json-content':
      return 'JSN'
    default:
      return 'SCH'
  }
}

export function RawExplorer({
  spaceId,
  branchId,
  spaceName,
}: {
  spaceId: string
  branchId: string
  spaceName: string
}) {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [kindFilter, setKindFilter] = useState('')
  const [badgeFilter, setBadgeFilter] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({
    key: 'name',
    asc: true,
  })

  const urlMode = params.get('mode')
  const mode: RawExplorerMode = MODES.includes(urlMode as RawExplorerMode)
    ? (urlMode as RawExplorerMode)
    : storedMode()
  const sourceSlug = params.get('source') ?? ''
  const path = params.get('path') ?? ''
  const selected = params.get('sel') ?? ''
  const view: View = params.get('view') === 'list' ? 'list' : 'tiles'

  /** Navigation : chaque changement de dossier est une entrée d'historique. */
  const go = (changes: Record<string, string | null>, replace = false) => {
    const next = new URLSearchParams(params)
    next.set('tab', 'raw')
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === '') next.delete(key)
      else next.set(key, value)
    }
    setParams(next, { replace })
  }

  const sources = useQuery({
    queryKey: ['rawSources', spaceId, branchId],
    queryFn: () => api.listRawExplorerSources(spaceId, branchId),
    staleTime: STALE_MS,
    retry: false,
  })
  const sourceList = useMemo(() => sources.data?.sources ?? [], [sources.data])
  const current: RawExplorerSource | undefined = sourceList.find((s) => s.slug === sourceSlug)

  // Sans source choisie (ou source absente de cette branche) : la première.
  useEffect(() => {
    if (!sources.data || sourceList.length === 0) return
    if (!current) go({ source: sourceList[0].slug, path: null, sel: null }, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources.data, sourceSlug])

  const tree = useQuery({
    queryKey: ['rawTree', spaceId, branchId, sourceSlug, mode],
    queryFn: () => api.getRawExplorerTree(spaceId, branchId, sourceSlug, mode),
    enabled: !!current && current.collected,
    staleTime: STALE_MS,
    retry: false,
  })

  const folder = useQuery({
    queryKey: ['rawFolder', spaceId, branchId, sourceSlug, mode, path],
    queryFn: () => api.getRawExplorerFolder(spaceId, branchId, sourceSlug, mode, path),
    enabled: !!current && current.collected,
    staleTime: STALE_MS,
    retry: false,
  })

  // Un dossier qui n'existe pas sur cette branche (changement de branche, lien
  // ancien) : on revient à la racine de la source plutôt que d'afficher une erreur.
  useEffect(() => {
    const err = folder.error
    if (
      err instanceof ApiRequestError &&
      err.body.error === 'raw_explorer_folder_not_found' &&
      path
    ) {
      go({ path: null, sel: null }, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder.error])

  useEffect(() => {
    const id = setTimeout(() => setDebounced(search.trim()), 300)
    return () => clearTimeout(id)
  }, [search])

  const results = useQuery({
    queryKey: ['rawSearch', spaceId, branchId, sourceSlug, mode, debounced],
    queryFn: () => api.searchRawExplorer(spaceId, branchId, sourceSlug, mode, debounced),
    enabled: !!current && current.collected && debounced.length > 0,
    staleTime: STALE_MS,
    retry: false,
  })

  const changeMode = (next: RawExplorerMode) => {
    storeMode(next)
    // Les dossiers diffèrent d'un mode à l'autre : on repart de la racine.
    go({ mode: next, path: null })
  }

  const folderLabel = (folderPath: string): string => {
    const name = lastSegment(folderPath)
    if (mode === 'kind') return t(`rawKind.${name}`, { defaultValue: name })
    if (mode === 'parser' && name === 'unknown') return t('rawParserUnknown')
    return name
  }

  const objects = useMemo(() => folder.data?.objects ?? [], [folder.data])
  const kinds = useMemo(() => [...new Set(objects.map((o) => o.kind))].sort(), [objects])
  const shownObjects = objects.filter(
    (o) =>
      (!kindFilter || o.kind === kindFilter) &&
      (!badgeFilter || o.badges.includes(badgeFilter as RawBadge)),
  )
  const filtering = !!kindFilter || !!badgeFilter
  const shownFolders = filtering ? [] : (folder.data?.folders ?? [])
  const shownDocuments = filtering ? [] : (folder.data?.documents ?? [])

  const messages = [sources.error, tree.error, results.error]
    .filter((e): e is Error => !!e)
    .flatMap((e) => errorMessages(t, e))
  if (
    folder.error &&
    !(
      folder.error instanceof ApiRequestError &&
      folder.error.body.error === 'raw_explorer_folder_not_found'
    )
  ) {
    messages.push(...errorMessages(t, folder.error))
  }

  if (sources.isLoading) {
    return (
      <div className="panel" aria-busy="true">
        <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
      </div>
    )
  }

  if (sourceList.length === 0) {
    return (
      <>
        <ErrorList messages={messages} />
        <div className="empty-note">
          {t('rawNoSource')} <Link to="/sources">{t('rawGoDeclared')}</Link>
        </div>
      </>
    )
  }

  const crumbs = path ? path.split('/') : []

  return (
    <div className="raw-explorer">
      <ErrorList messages={messages} />
      <aside className="panel raw-tree" aria-label={t('rawTreeLabel')}>
        <label className="field-label" htmlFor="raw-mode">
          {t('rawModeLabel')}
        </label>
        <select
          id="raw-mode"
          className="select"
          value={mode}
          onChange={(e) => changeMode(e.target.value as RawExplorerMode)}
        >
          {MODES.map((m) => (
            <option key={m} value={m}>
              {t(`rawMode.${m}`)}
            </option>
          ))}
        </select>
        <ul className="raw-tree-list" role="tree">
          {sourceList.map((s) => (
            <li key={s.slug} role="treeitem" aria-expanded={s.slug === sourceSlug}>
              <button
                type="button"
                className={`raw-tree-node${s.slug === sourceSlug && !path ? ' active' : ''}`}
                onClick={() => go({ source: s.slug, path: null, sel: null })}
              >
                <span className="raw-tree-glyph">SRC</span>
                {s.label}
                <span className="raw-count">{s.objects}</span>
              </button>
              {s.slug === sourceSlug && tree.data ? (
                <FolderBranch
                  folders={tree.data.folders}
                  parent=""
                  current={path}
                  label={folderLabel}
                  onOpen={(p) => go({ path: p, sel: null })}
                />
              ) : null}
            </li>
          ))}
        </ul>
      </aside>

      <section className="raw-main">
        <div className="raw-toolbar">
          <nav className="breadcrumb raw-crumbs" aria-label={t('rawBreadcrumb')}>
            <span>{spaceName}</span>
            {' › '}
            <button
              type="button"
              className="btn-link"
              onClick={() => go({ path: null, sel: null })}
            >
              {current?.label ?? sourceSlug}
            </button>
            {crumbs.map((_part, i) => {
              const target = crumbs.slice(0, i + 1).join('/')
              return (
                <span key={target}>
                  {' › '}
                  <button
                    type="button"
                    className="btn-link"
                    onClick={() => go({ path: target, sel: null })}
                  >
                    {folderLabel(target)}
                  </button>
                </span>
              )
            })}
          </nav>
          <div className="raw-view-toggle" role="group" aria-label={t('rawViewLabel')}>
            <button
              type="button"
              className={`btn btn-sm${view === 'tiles' ? ' btn-primary' : ''}`}
              aria-pressed={view === 'tiles'}
              onClick={() => go({ view: null }, true)}
            >
              {t('rawViewTiles')}
            </button>
            <button
              type="button"
              className={`btn btn-sm${view === 'list' ? ' btn-primary' : ''}`}
              aria-pressed={view === 'list'}
              onClick={() => go({ view: 'list' }, true)}
            >
              {t('rawViewList')}
            </button>
            <button type="button" className="btn btn-sm" disabled title={t('rawGraphSoon')}>
              {t('rawViewGraph')}
            </button>
          </div>
        </div>

        {current && current.collected ? (
          <>
            <div className="raw-filters">
              <div className="raw-search">
                <input
                  className="input"
                  type="search"
                  placeholder={t('rawSearchPlaceholder')}
                  aria-label={t('rawSearchPlaceholder')}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {debounced && results.data ? (
                  <ul className="raw-search-results" role="listbox">
                    {results.data.results.length === 0 ? (
                      <li className="muted">{t('rawSearchNone')}</li>
                    ) : (
                      results.data.results.map((r) => (
                        <li key={r.file}>
                          <button
                            type="button"
                            className="raw-search-hit"
                            onClick={() => {
                              setSearch('')
                              setDebounced('')
                              go({ path: r.folder, sel: r.file })
                            }}
                          >
                            <span className="raw-glyph-sm">{kindGlyph(r.kind)}</span>
                            {r.title}
                            <span className="muted mono">
                              {r.folder ? folderLabel(r.folder) : '/'}
                            </span>
                          </button>
                        </li>
                      ))
                    )}
                  </ul>
                ) : null}
              </div>
              <select
                className="select"
                aria-label={t('rawFilterKind')}
                value={kindFilter}
                onChange={(e) => setKindFilter(e.target.value)}
              >
                <option value="">{t('rawFilterKindAll')}</option>
                {kinds.map((k) => (
                  <option key={k} value={k}>
                    {t(`rawKind.${k}`, { defaultValue: k })}
                  </option>
                ))}
              </select>
              <select
                className="select"
                aria-label={t('rawFilterState')}
                value={badgeFilter}
                onChange={(e) => setBadgeFilter(e.target.value)}
              >
                <option value="">{t('rawFilterStateAll')}</option>
                {BADGES.map((b) => (
                  <option key={b} value={b}>
                    {t(`rawBadge.${b}`)}
                  </option>
                ))}
              </select>
            </div>
            <CollectLine source={tree.data ?? current} />
          </>
        ) : null}

        {!current ? null : !current.collected ? (
          <div className="empty-note">
            {t('rawNotCollected')} <Link to={`/sources/${current.slug}`}>{t('rawGoCollect')}</Link>
          </div>
        ) : folder.isLoading ? (
          <div className="panel" aria-busy="true">
            <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
          </div>
        ) : folder.data &&
          shownFolders.length + shownObjects.length + shownDocuments.length === 0 ? (
          <div className="empty-note">{filtering ? t('rawFilterEmpty') : t('rawFolderEmpty')}</div>
        ) : folder.data && view === 'tiles' ? (
          <Tiles
            folders={shownFolders}
            objects={shownObjects}
            documents={shownDocuments}
            selected={selected}
            folderLabel={folderLabel}
            onOpenFolder={(p) => go({ path: p, sel: null })}
            onSelect={(file) => go({ sel: file }, true)}
          />
        ) : folder.data ? (
          <ListView
            folders={shownFolders}
            objects={shownObjects}
            documents={shownDocuments}
            selected={selected}
            sort={sort}
            onSort={(key) => setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))}
            folderLabel={folderLabel}
            onOpenFolder={(p) => go({ path: p, sel: null })}
            onSelect={(file) => go({ sel: file }, true)}
          />
        ) : null}
        {path && folder.data ? (
          <button
            type="button"
            className="btn btn-sm raw-up"
            onClick={() => go({ path: parentOf(path) || null, sel: null })}
          >
            {t('rawUp')}
          </button>
        ) : null}
      </section>
    </div>
  )
}

function FolderBranch({
  folders,
  parent,
  current,
  label,
  onOpen,
}: {
  folders: RawFolderItem[]
  parent: string
  current: string
  label: (path: string) => string
  onOpen: (path: string) => void
}) {
  const children = folders.filter((f) => f.path !== '' && parentOf(f.path) === parent)
  if (children.length === 0) return null
  return (
    <ul role="group" className="raw-tree-list">
      {children.map((f) => {
        // Seule la branche du dossier courant est dépliée : un dépôt de
        // centaines de dossiers ne s'affiche pas d'un bloc.
        const open = current === f.path || current.startsWith(`${f.path}/`)
        return (
          <li key={f.path} role="treeitem" aria-expanded={open}>
            <button
              type="button"
              className={`raw-tree-node${current === f.path ? ' active' : ''}`}
              onClick={() => onOpen(f.path)}
            >
              <span className="raw-tree-glyph">{open ? '▾' : '▸'}</span>
              {label(f.path)}
              <span className="raw-count">{f.objects}</span>
            </button>
            {open ? (
              <FolderBranch
                folders={folders}
                parent={f.path}
                current={current}
                label={label}
                onOpen={onOpen}
              />
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

function CollectLine({ source }: { source: RawExplorerSource }) {
  const { t, i18n } = useTranslation()
  const last = source.last_collect
  return (
    <p className="raw-collect muted">
      {last
        ? t('rawLastCollect', {
            date: new Date(last.date).toLocaleString(i18n.language),
            objects: source.objects,
            errors: last.errors,
          })
        : t('rawObjectsOnly', { objects: source.objects })}
    </p>
  )
}

function Badges({ obj }: { obj: RawObjectItem }) {
  const { t } = useTranslation()
  if (obj.badges.length === 0) return null
  return (
    <span className="raw-badges">
      {obj.badges.map((b) => (
        <span
          key={b}
          className={`raw-badge raw-badge-${b}`}
          title={b === 'excluded' && obj.excluded_by ? obj.excluded_by : undefined}
        >
          {t(`rawBadge.${b}`)}
        </span>
      ))}
    </span>
  )
}

interface ContentProps {
  folders: RawFolderItem[]
  objects: RawObjectItem[]
  documents: RawDocumentItem[]
  selected: string
  folderLabel: (path: string) => string
  onOpenFolder: (path: string) => void
  onSelect: (file: string) => void
}

function Tiles({
  folders,
  objects,
  documents,
  selected,
  folderLabel,
  onOpenFolder,
  onSelect,
}: ContentProps) {
  const { t } = useTranslation()
  return (
    <div className="raw-sections">
      {folders.length > 0 ? (
        <section>
          <h2 className="panel-label">{t('rawSectionFolders')}</h2>
          <div className="raw-tiles">
            {folders.map((f) => (
              <button
                key={f.path}
                type="button"
                className="raw-tile"
                onClick={() => onOpenFolder(f.path)}
              >
                <span className="raw-glyph raw-glyph-folder">DIR</span>
                <span className="raw-tile-name">{folderLabel(f.path)}</span>
                <span className="raw-tile-sub">{t('rawItems', { count: f.items })}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {objects.length > 0 ? (
        <section>
          <h2 className="panel-label">{t('rawSectionObjects')}</h2>
          <div className="raw-tiles">
            {objects.map((o) => (
              <button
                key={o.file}
                type="button"
                className={`raw-tile${selected === o.file ? ' selected' : ''}`}
                aria-pressed={selected === o.file}
                onClick={() => onSelect(o.file)}
              >
                <span className="raw-glyph">{kindGlyph(o.kind)}</span>
                <span className="raw-tile-name">{o.title}</span>
                <span className="raw-tile-sub">
                  {t('rawFields', { count: o.fields })}
                  {o.origin ? ` · ${lastSegment(o.origin)}` : ''}
                </span>
                <Badges obj={o} />
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {documents.length > 0 ? (
        <section>
          <h2 className="panel-label">{t('rawSectionDocuments')}</h2>
          <div className="raw-tiles">
            {documents.map((d) => (
              <button
                key={d.path}
                type="button"
                className={`raw-tile${selected === d.path ? ' selected' : ''}`}
                aria-pressed={selected === d.path}
                onClick={() => onSelect(d.path)}
              >
                <span className="raw-glyph raw-glyph-doc">DOC</span>
                <span className="raw-tile-name">{d.title}</span>
                <span className="raw-tile-sub">{t('rawSections', { count: d.sections })}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}

function ListView({
  folders,
  objects,
  documents,
  selected,
  sort,
  onSort,
  folderLabel,
  onOpenFolder,
  onSelect,
}: ContentProps & {
  sort: { key: SortKey; asc: boolean }
  onSort: (key: SortKey) => void
}) {
  const { t } = useTranslation()
  const sorted = useMemo(() => {
    const value = (o: RawObjectItem): string | number => {
      switch (sort.key) {
        case 'kind':
          return o.kind
        case 'fields':
          return o.fields
        case 'origin':
          return o.origin
        case 'state':
          return o.badges.join(',')
        default:
          return o.title.toLowerCase()
      }
    }
    return [...objects].sort((a, b) => {
      const va = value(a)
      const vb = value(b)
      const cmp =
        typeof va === 'number' && typeof vb === 'number'
          ? va - vb
          : String(va).localeCompare(String(vb))
      return sort.asc ? cmp : -cmp
    })
  }, [objects, sort])

  const header = (key: SortKey, label: string) => (
    <th aria-sort={sort.key === key ? (sort.asc ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="btn-link" onClick={() => onSort(key)}>
        {label}
        {sort.key === key ? (sort.asc ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  )

  return (
    <div className="panel">
      <table className="table">
        <thead>
          <tr>
            {header('name', t('rawColName'))}
            {header('kind', t('rawColKind'))}
            {header('fields', t('rawColFields'))}
            {header('origin', t('rawColOrigin'))}
            {header('state', t('rawColState'))}
          </tr>
        </thead>
        <tbody>
          {folders.map((f) => (
            <tr key={`d:${f.path}`} className="row-clickable" onClick={() => onOpenFolder(f.path)}>
              <td>
                <span className="raw-glyph-sm raw-glyph-folder">DIR</span> {folderLabel(f.path)}
              </td>
              <td className="muted">{t('rawKindFolder')}</td>
              <td className="muted">{t('rawItems', { count: f.items })}</td>
              <td />
              <td />
            </tr>
          ))}
          {sorted.map((o) => (
            <tr
              key={o.file}
              className={`row-clickable${selected === o.file ? ' selected' : ''}`}
              onClick={() => onSelect(o.file)}
            >
              <td>
                <span className="raw-glyph-sm">{kindGlyph(o.kind)}</span> {o.title}
              </td>
              <td>{t(`rawKind.${o.kind}`, { defaultValue: o.kind })}</td>
              <td>{o.fields}</td>
              <td className="mono">{o.origin}</td>
              <td>
                <Badges obj={o} />
              </td>
            </tr>
          ))}
          {documents.map((d) => (
            <tr
              key={`doc:${d.path}`}
              className={`row-clickable${selected === d.path ? ' selected' : ''}`}
              onClick={() => onSelect(d.path)}
            >
              <td>
                <span className="raw-glyph-sm raw-glyph-doc">DOC</span> {d.title}
              </td>
              <td className="muted">{t('rawKindDocument')}</td>
              <td className="muted">{t('rawSections', { count: d.sections })}</td>
              <td className="mono">{d.path}</td>
              <td />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
