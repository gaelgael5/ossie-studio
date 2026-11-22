import {
  type CSSProperties,
  type PointerEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
  RawTarget,
  SourceItem,
} from '../apiClient'
import { errorMessages } from '../errors'
import { ApiRequestError } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { BusyButton } from '../components/BusyButton'
import { RawPreview, TagBadges, VisibilityBadge, isObjectSelection } from './RawPreview'
import { RawGraph } from './RawGraph'
import { RawDocumentPage, RawExclusive } from './RawExclusive'
import {
  DEFAULT_EXCLUSIONS,
  excludingEntry,
  exclusionsOf,
  withExclusion,
  withoutExclusion,
} from '../sourceExclusions'

// Explorateur raw : parcourir la couche raw d'un espace comme un explorateur de
// fichiers, en lecture seule. L'état de navigation (source, mode, dossier, objet
// sélectionné, affichage) vit dans l'adresse : elle se partage, rouvre le même
// dossier, et le bouton retour du navigateur revient au dossier précédent.

const MODES: RawExplorerMode[] = ['origin', 'kind', 'parser']
const VISIBILITIES = ['public', 'internal', 'protected', 'private', 'unknown']
const BADGES: RawBadge[] = ['retained', 'new', 'modified', 'excluded', 'error']
const MODE_KEY = 'ossie.rawExplorer.mode'
const WIDTH_KEY = 'ossie.rawExplorer.treeWidth'
const PREVIEW_KEY = 'ossie.rawExplorer.preview'
const TREE_MIN = 180
const TREE_MAX = 720
const TREE_DEFAULT = 300

function clampWidth(value: number): number {
  return Math.min(TREE_MAX, Math.max(TREE_MIN, Math.round(value)))
}

function asParsers(parameters: Record<string, unknown> | undefined): string[] {
  const value = parameters?.parsers
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

function storedPreviewOpen(): boolean {
  try {
    return localStorage.getItem(PREVIEW_KEY) !== 'closed'
  } catch {
    return true
  }
}

function storedWidth(): number {
  try {
    const value = Number(localStorage.getItem(WIDTH_KEY))
    return value ? clampWidth(value) : TREE_DEFAULT
  } catch {
    return TREE_DEFAULT
  }
}
// Chaque lecture réaligne la copie sur la forge côté serveur : on ne relit pas
// un dossier déjà vu à chaque clic.
const STALE_MS = 30_000

type View = 'tiles' | 'list' | 'graph'
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
  const [tagFilter, setTagFilter] = useState('')
  const [visFilter, setVisFilter] = useState<string[]>([])
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({
    key: 'name',
    asc: true,
  })
  const [treeWidth, setTreeWidth] = useState(storedWidth)
  const [previewOpen, setPreviewOpen] = useState(storedPreviewOpen)
  const togglePreview = (open: boolean) => {
    setPreviewOpen(open)
    try {
      localStorage.setItem(PREVIEW_KEY, open ? 'open' : 'closed')
    } catch {
      // stockage indisponible : le choix vaut pour cette visite
    }
  }
  // L'explorateur occupe exactement la hauteur restante de la fenêtre : l'arbre
  // et le contenu défilent chacun de leur côté, sans faire défiler la page (un
  // arbre de centaines de dossiers repoussait sinon les vignettes hors de vue).
  const frame = useRef<HTMLDivElement>(null)
  const [frameHeight, setFrameHeight] = useState<number | null>(null)
  useLayoutEffect(() => {
    const measure = () => {
      const el = frame.current
      if (!el) return
      const top = el.getBoundingClientRect().top + window.scrollY
      setFrameHeight(Math.max(320, window.innerHeight - top - 16))
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  })

  // Largeur de l'arbre réglable à la souris (ou aux flèches), retenue d'une
  // visite à l'autre : les noms de dossiers d'un dépôt .NET sont longs.
  const resizeTree = (value: number) => {
    const width = clampWidth(value)
    setTreeWidth(width)
    try {
      localStorage.setItem(WIDTH_KEY, String(width))
    } catch {
      // stockage indisponible : la largeur vaut pour cette visite
    }
  }
  const startResize = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = treeWidth
    const move = (ev: globalThis.PointerEvent) => resizeTree(startWidth + ev.clientX - startX)
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      document.body.classList.remove('resizing-x')
    }
    document.body.classList.add('resizing-x')
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const urlMode = params.get('mode')
  const mode: RawExplorerMode = MODES.includes(urlMode as RawExplorerMode)
    ? (urlMode as RawExplorerMode)
    : storedMode()
  const sourceSlug = params.get('source') ?? ''
  const path = params.get('path') ?? ''
  const selected = params.get('sel') ?? ''
  const opened = params.get('open') ?? ''
  const viewParam = params.get('view')
  const view: View = viewParam === 'list' || viewParam === 'graph' ? viewParam : 'tiles'
  // Centre du graphe : `center` (« source/fichier ») s'il est posé — un clic sur
  // un nœud sélectionne sans recentrer — sinon l'objet sélectionné.
  const centerParam = params.get('center')
  const graphCenter = centerParam?.includes('/')
    ? {
        slug: centerParam.slice(0, centerParam.indexOf('/')),
        file: centerParam.slice(centerParam.indexOf('/') + 1),
      }
    : selected && isObjectSelection(selected)
      ? { slug: sourceSlug, file: selected }
      : null

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

  // Le graphe est centré sur un objet : ouvrir un dossier (arbre, fil d'Ariane,
  // source, mode) le laisserait vide. On revient donc à la vue d'où l'on était
  // entré dans le graphe (`back`), vignettes par défaut.
  const leaveGraph = (): Record<string, string | null> =>
    view === 'graph'
      ? { view: params.get('back') === 'list' ? 'list' : null, back: null, center: null }
      : {}
  /** Navigation vers un dossier : quitte le graphe s'il est affiché. */
  const goFolder = (changes: Record<string, string | null>, replace = false) =>
    go({ ...changes, ...leaveGraph() }, replace)
  /** Entrée dans le graphe en retenant la vue à retrouver ensuite. */
  const graphEntry = (): Record<string, string | null> => ({
    view: 'graph',
    center: null,
    back: view === 'list' ? 'list' : null,
  })

  const sources = useQuery({
    queryKey: ['rawSources', spaceId, branchId],
    queryFn: () => api.listRawExplorerSources(spaceId, branchId),
    staleTime: STALE_MS,
    retry: false,
  })
  const sourceList = useMemo(() => sources.data?.sources ?? [], [sources.data])
  const current: RawExplorerSource | undefined = sourceList.find((s) => s.slug === sourceSlug)

  // Configuration de la source (manifeste) : ses répertoires exclus se voient
  // dans l'arbre et se règlent d'ici. Les exclusions ne valent que pour une
  // source Git, dans l'arborescence d'origine (des dossiers du dépôt).
  const declared = useQuery({
    queryKey: ['sources', spaceId, branchId],
    queryFn: () => api.listSources(spaceId, branchId),
    staleTime: STALE_MS,
    retry: false,
  })
  const declaredSource = declared.data?.sources?.find((s) => s.slug === sourceSlug)
  const exclusions = exclusionsOf(declaredSource?.parameters)
  const exclusionsApply = current?.type === 'git' && mode === 'origin' && !!declaredSource?.ok
  const excludedBy = (folderPath: string): string | null =>
    exclusionsApply && folderPath ? excludingEntry(folderPath, exclusions) : null

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
      goFolder({ path: null, sel: null }, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder.error])

  // Un filtre ne survit pas au changement de dossier : il masquerait tout dans
  // un dossier où sa valeur n'existe pas.
  useEffect(() => {
    setKindFilter('')
    setBadgeFilter('')
    setTagFilter('')
    setVisFilter([])
  }, [sourceSlug, mode, path])

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
    goFolder({ mode: next, path: null })
  }

  const folderLabel = (folderPath: string): string => {
    const name = lastSegment(folderPath)
    if (mode === 'kind') return t(`rawKind.${name}`, { defaultValue: name })
    if (mode === 'parser' && name === 'unknown') return t('rawParserUnknown')
    return name
  }

  const objects = useMemo(() => folder.data?.objects ?? [], [folder.data])
  const kinds = useMemo(() => [...new Set(objects.map((o) => o.kind))].sort(), [objects])
  const presentVisibilities = useMemo(
    () => VISIBILITIES.filter((v) => objects.some((o) => o.visibility === v)),
    [objects],
  )
  // Seuls les états réellement portés par un objet du dossier sont proposés :
  // un filtre sans choix utile ne s'affiche pas.
  const presentBadges = useMemo(
    () => BADGES.filter((b) => objects.some((o) => o.badges.includes(b))),
    [objects],
  )
  // Tags gouvernés présents dans le dossier : comme l'état, « tous » + au moins un tag.
  const presentTags = useMemo(
    () => [...new Set(objects.flatMap((o) => o.tags.map((tag) => tag.tag)))].sort(),
    [objects],
  )
  const shownObjects = objects.filter(
    (o) =>
      (!kindFilter || o.kind === kindFilter) &&
      (!tagFilter || o.tags.some((tag) => tag.tag === tagFilter)) &&
      (!badgeFilter || o.badges.includes(badgeFilter as RawBadge)) &&
      // Rien de coché : tout s'affiche (tables et contrats compris, sans visibilité).
      (visFilter.length === 0 || visFilter.includes(o.visibility)),
  )
  const filtering = !!kindFilter || !!badgeFilter || !!tagFilter || visFilter.length > 0
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

  /** Ouvre une cible (référence, document cité) : sa source, son dossier, sélectionnée. */
  const openTarget = (target: RawTarget) => {
    let folder = target.folder
    if (!isObjectSelection(target.file)) {
      // Document : son dossier dépend du mode (cf. regroupement côté serveur).
      const type = sourceList.find((s) => s.slug === target.slug)?.type
      folder =
        mode === 'kind'
          ? 'documents'
          : mode === 'parser'
            ? 'markdown'
            : type === 'git'
              ? parentOf(target.file)
              : ''
    }
    go({ source: target.slug, path: folder || null, sel: target.file })
  }

  // Vue exclusive (?open=…) : à la place de l'arbre et des vignettes. L'état de
  // l'explorateur (filtres, tri, largeur) reste vivant derrière : « Retour aux
  // vignettes » et le bouton retour du navigateur retrouvent tout.
  if (opened && current?.collected) {
    const back = () => go({ open: null, sel: opened })
    if (!isObjectSelection(opened)) {
      return (
        <RawDocumentPage
          spaceId={spaceId}
          branchId={branchId}
          slug={sourceSlug}
          path={opened}
          onBack={back}
        />
      )
    }
    const folderParams = new URLSearchParams(params)
    folderParams.delete('open')
    return (
      <RawExclusive
        key={opened}
        spaceId={spaceId}
        branchId={branchId}
        slug={sourceSlug}
        mode={mode}
        file={opened}
        siblings={shownObjects.map((o) => o.file)}
        folderHref={`/sources?${folderParams.toString()}`}
        markdownParser={asParsers(declaredSource?.parameters).includes('markdown')}
        onBack={back}
        onNavigate={(file) => go({ open: file, sel: file }, true)}
        onOpenTarget={(target) =>
          isObjectSelection(target.file)
            ? go({
                source: target.slug,
                path: target.folder || null,
                sel: target.file,
                open: target.file,
              })
            : openTarget(target)
        }
        onGraph={() => go({ open: null, sel: opened, ...graphEntry() })}
      />
    )
  }

  return (
    <>
      <ErrorList messages={messages} />
      <div
        ref={frame}
        className={`raw-explorer${previewOpen && current?.collected ? ' with-preview' : ''}`}
        style={
          {
            '--raw-tree-width': `${treeWidth}px`,
            ...(frameHeight ? { height: `${frameHeight}px` } : {}),
          } as CSSProperties
        }
      >
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
                  onClick={() => goFolder({ source: s.slug, path: null, sel: null })}
                >
                  <span className="raw-tree-glyph">SRC</span>
                  {s.label}
                  <span className="raw-count">{s.objects + s.documents}</span>
                </button>
                {s.slug === sourceSlug && tree.data ? (
                  <FolderBranch
                    folders={tree.data.folders}
                    parent=""
                    current={path}
                    label={folderLabel}
                    onOpen={(p) => goFolder({ path: p, sel: null })}
                    excludedBy={excludedBy}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        </aside>
        <div
          className="raw-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label={t('rawResizeTree')}
          aria-valuenow={treeWidth}
          aria-valuemin={TREE_MIN}
          aria-valuemax={TREE_MAX}
          tabIndex={0}
          onPointerDown={startResize}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') resizeTree(treeWidth - 20)
            if (e.key === 'ArrowRight') resizeTree(treeWidth + 20)
          }}
        />

        <section className="raw-main">
          <div className="raw-toolbar">
            <nav className="breadcrumb raw-crumbs" aria-label={t('rawBreadcrumb')}>
              <span>{spaceName}</span>
              {' › '}
              <button
                type="button"
                className="btn-link"
                onClick={() => goFolder({ path: null, sel: null })}
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
                      onClick={() => goFolder({ path: target, sel: null })}
                    >
                      {folderLabel(target)}
                    </button>
                  </span>
                )
              })}
            </nav>
            <div className="raw-view-toggle" role="group" aria-label={t('rawViewLabel')}>
              {!previewOpen && current?.collected ? (
                <button type="button" className="btn btn-sm" onClick={() => togglePreview(true)}>
                  {t('rawPreviewOpen')}
                </button>
              ) : null}
              <button
                type="button"
                className={`btn btn-sm${view === 'tiles' ? ' btn-primary' : ''}`}
                aria-pressed={view === 'tiles'}
                onClick={() => go({ view: null, back: null }, true)}
              >
                {t('rawViewTiles')}
              </button>
              <button
                type="button"
                className={`btn btn-sm${view === 'list' ? ' btn-primary' : ''}`}
                aria-pressed={view === 'list'}
                onClick={() => go({ view: 'list', back: null }, true)}
              >
                {t('rawViewList')}
              </button>
              <button
                type="button"
                className={`btn btn-sm${view === 'graph' ? ' btn-primary' : ''}`}
                aria-pressed={view === 'graph'}
                onClick={() => go(graphEntry(), true)}
              >
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
                {kinds.length > 1 ? (
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
                ) : null}
                {presentBadges.length > 0 ? (
                  <select
                    className="select"
                    aria-label={t('rawFilterState')}
                    value={badgeFilter}
                    onChange={(e) => setBadgeFilter(e.target.value)}
                  >
                    <option value="">{t('rawFilterStateAll')}</option>
                    {presentBadges.map((b) => (
                      <option key={b} value={b}>
                        {t(`rawBadge.${b}`)}
                      </option>
                    ))}
                  </select>
                ) : null}
                {presentTags.length > 0 ? (
                  <select
                    className="select"
                    aria-label={t('rawFilterTag')}
                    value={tagFilter}
                    onChange={(e) => setTagFilter(e.target.value)}
                  >
                    <option value="">{t('rawFilterTagAll')}</option>
                    {presentTags.map((tag) => (
                      <option key={tag} value={tag}>
                        {tag}
                      </option>
                    ))}
                  </select>
                ) : null}
                {presentVisibilities.length > 0 ? (
                  <fieldset className="raw-vis-filter">
                    <legend>{t('rawFilterVisibility')}</legend>
                    {presentVisibilities.map((v) => (
                      <label key={v}>
                        <input
                          type="checkbox"
                          checked={visFilter.includes(v)}
                          onChange={(e) =>
                            setVisFilter((cur) =>
                              e.target.checked ? [...cur, v] : cur.filter((x) => x !== v),
                            )
                          }
                        />{' '}
                        {t(`rawVisibility.${v}`)}
                      </label>
                    ))}
                  </fieldset>
                ) : null}
              </div>
              <CollectLine source={tree.data ?? current} />
              {exclusionsApply && path && declaredSource ? (
                <ExclusionBar
                  key={`${sourceSlug}/${path}`}
                  spaceId={spaceId}
                  branchId={branchId}
                  source={declaredSource}
                  folder={path}
                  entry={excludedBy(path)}
                  exclusions={exclusions}
                />
              ) : null}
            </>
          ) : null}

          {!current ? null : !current.collected ? (
            <div className="empty-note">
              {t('rawNotCollected')}{' '}
              <Link to={`/sources/${current.slug}`}>{t('rawGoCollect')}</Link>
            </div>
          ) : view === 'graph' ? (
            graphCenter ? (
              <RawGraph
                spaceId={spaceId}
                branchId={branchId}
                mode={mode}
                center={graphCenter}
                onSelect={(n) =>
                  go({
                    source: n.slug,
                    path: n.folder || null,
                    sel: n.file,
                    center: `${graphCenter.slug}/${graphCenter.file}`,
                  })
                }
                onRecenter={(n) =>
                  go({ source: n.slug, path: n.folder || null, sel: n.file, center: null })
                }
              />
            ) : (
              <div className="empty-note">{t('rawGraphPick')}</div>
            )
          ) : folder.isLoading ? (
            <div className="panel" aria-busy="true">
              <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
            </div>
          ) : folder.data &&
            shownFolders.length + shownObjects.length + shownDocuments.length === 0 ? (
            <div className="empty-note">
              {filtering ? t('rawFilterEmpty') : t('rawFolderEmpty')}
            </div>
          ) : folder.data && view === 'tiles' ? (
            <Tiles
              folders={shownFolders}
              objects={shownObjects}
              documents={shownDocuments}
              selected={selected}
              folderLabel={folderLabel}
              onOpenFolder={(p) => go({ path: p, sel: null })}
              onSelect={(file) => go({ sel: file }, true)}
              onOpenItem={(file) => go({ open: file, sel: file })}
              excludedBy={excludedBy}
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
              onOpenItem={(file) => go({ open: file, sel: file })}
            />
          ) : null}
          {path && folder.data ? (
            <button
              type="button"
              className="btn btn-sm raw-up"
              onClick={() => goFolder({ path: parentOf(path) || null, sel: null })}
            >
              {t('rawUp')}
            </button>
          ) : null}
        </section>
        {previewOpen && current?.collected ? (
          <RawPreview
            spaceId={spaceId}
            branchId={branchId}
            slug={sourceSlug}
            mode={mode}
            selected={selected}
            folderObjects={objects}
            onOpen={openTarget}
            onCollapse={() => togglePreview(false)}
          />
        ) : null}
      </div>
    </>
  )
}

/**
 * Exclure le dossier courant de la source (ou le réinclure). Écrit le manifeste
 * de la source dans le brouillon, comme l'écran de configuration : l'exclusion
 * se voit dans la configuration et la collecte suivante retire de raw/ ce qui
 * en venait. Confirmation avant d'écrire.
 */
function ExclusionBar({
  spaceId,
  branchId,
  source,
  folder,
  entry,
  exclusions,
}: {
  spaceId: string
  branchId: string
  source: SourceItem
  folder: string
  entry: string | null
  exclusions: string[]
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [messages, setMessages] = useState<string[]>([])
  const save = useMutation({
    mutationFn: (next: string[]) => {
      const rest = { ...(source.parameters ?? {}) }
      delete rest.exclusions
      return api.updateSource(spaceId, branchId, source.slug, {
        label: source.label ?? source.slug,
        connection: source.connection?.slug ?? '',
        parameters: next.length > 0 ? { ...rest, exclusions: next } : rest,
      })
    },
    onSuccess: async () => {
      setConfirming(false)
      setMessages([])
      await queryClient.invalidateQueries({ queryKey: ['sources'] })
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const isDefault = entry !== null && DEFAULT_EXCLUSIONS.includes(entry)
  return (
    <div className="raw-exclusion">
      <ErrorList messages={messages} />
      {entry ? (
        <p className="raw-exclusion-note">
          {t('rawExcludedNote', { entry })}{' '}
          {isDefault ? (
            <span className="muted">{t('rawExcludedDefault')}</span>
          ) : (
            <BusyButton
              type="button"
              className="btn btn-sm"
              busy={save.isPending}
              busyText={t('rawSaving')}
              onClick={() => save.mutate(withoutExclusion(exclusions, entry))}
            >
              {t('rawReinclude')}
            </BusyButton>
          )}
        </p>
      ) : confirming ? (
        <p className="raw-exclusion-note">
          {t('rawExcludeConfirm', { folder })}{' '}
          <BusyButton
            type="button"
            className="btn btn-sm btn-danger"
            busy={save.isPending}
            busyText={t('rawSaving')}
            onClick={() => save.mutate(withExclusion(exclusions, folder))}
          >
            {t('rawExcludeDo')}
          </BusyButton>{' '}
          <button type="button" className="btn btn-sm" onClick={() => setConfirming(false)}>
            {t('cancel')}
          </button>
        </p>
      ) : (
        <button type="button" className="btn btn-sm" onClick={() => setConfirming(true)}>
          {t('rawExcludeFolder')}
        </button>
      )}
    </div>
  )
}

function FolderBranch({
  folders,
  parent,
  current,
  label,
  onOpen,
  excludedBy,
}: {
  folders: RawFolderItem[]
  parent: string
  current: string
  label: (path: string) => string
  onOpen: (path: string) => void
  excludedBy: (path: string) => string | null
}) {
  const { t } = useTranslation()
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
              className={`raw-tree-node${current === f.path ? ' active' : ''}${excludedBy(f.path) ? ' excluded' : ''}`}
              onClick={() => onOpen(f.path)}
              title={excludedBy(f.path) ? t('rawExcludedTitle') : undefined}
            >
              <span className="raw-tree-glyph">{open ? '▾' : '▸'}</span>
              {label(f.path)}
              <span className="raw-count">{f.files}</span>
            </button>
            {open ? (
              <FolderBranch
                folders={folders}
                parent={f.path}
                current={current}
                label={label}
                onOpen={onOpen}
                excludedBy={excludedBy}
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
  if (obj.badges.length === 0 && !obj.visibility && obj.tags.length === 0) return null
  return (
    <span className="raw-badges">
      <VisibilityBadge visibility={obj.visibility} source={obj.visibility_source} />
      <TagBadges tags={obj.tags} />
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
  excludedBy?: (path: string) => string | null
  /** Double-clic : vue exclusive d'un objet, ou document en pleine page. */
  onOpenItem?: (file: string) => void
}

function Tiles({
  folders,
  objects,
  documents,
  selected,
  folderLabel,
  onOpenFolder,
  onSelect,
  excludedBy,
  onOpenItem,
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
                {excludedBy?.(f.path) ? (
                  <span className="raw-badges">
                    <span className="raw-badge raw-badge-excluded">{t('rawExcludedBadge')}</span>
                  </span>
                ) : null}
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
                onDoubleClick={() => onOpenItem?.(o.file)}
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
                onDoubleClick={() => onOpenItem?.(d.path)}
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
  onOpenItem,
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
              onDoubleClick={() => onOpenItem?.(o.file)}
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
              onDoubleClick={() => onOpenItem?.(d.path)}
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
