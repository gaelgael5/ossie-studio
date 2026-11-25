import {
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
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
  RawContractItem,
  RawDocumentItem,
  RawExplorerMode,
  RawExplorerSource,
  RawFolderItem,
  RawTreeModule,
  RawObjectItem,
  RawTarget,
  SourceItem,
} from '../apiClient'
import { errorMessages } from '../errors'
import { ApiRequestError } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { BusyButton } from '../components/BusyButton'
import { refreshRawQueries } from '../components/SourceExclusions'
import { RawPreview, TagBadges, VisibilityBadge, isObjectSelection } from './RawPreview'
import { RawApplication } from './RawApplication'
import { RawModulePreview } from './RawModulePreview'
import { RefMenu } from './RawRefMenu'
import { RawGraph } from './RawGraph'
import { RawDocumentPage, RawExclusive } from './RawExclusive'
import { FULL_CONTRACT_NAME, RawContractPage, isContractSelection } from './RawContractPage'
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
// Tags montrés sur une vignette au repos ; les autres apparaissent au survol.
const TILE_TAGS = 2
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

// « Masquer les écartés » : retenu dans le navigateur, comme le mode d'affichage.
const HIDE_EXCLUDED_KEY = 'ossie.rawExplorer.hideExcluded'

function storedHideExcluded(): boolean {
  try {
    return localStorage.getItem(HIDE_EXCLUDED_KEY) === 'yes'
  } catch {
    return false
  }
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

// La vue « Revue de la source » a disparu (ticket docflow aad1d3c6) : un ancien
// lien `view=review` ouvre les vignettes.
type View = 'tiles' | 'list' | 'graph' | 'application'
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
    case 'module':
      return 'MOD'
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
  // Critères de recherche (ticket docflow be0f301c) : dans l'adresse, pour
  // qu'un lien filtré se partage et que le retour arrière les retrouve.
  const fText = params.get('q') ?? ''
  const kindFilter = params.get('fkind') ?? ''
  // Famille (ticket 915c76d2) : schema | openapi | markdown. Pas un critère de
  // recherche à elle seule : elle filtre aussi le dossier ouvert.
  const familyFilter = params.get('ffamily') ?? ''
  const badgeFilter = params.get('fstate') ?? ''
  const tagFilter = params.get('ftag') ?? ''
  // « Objets modifiés » (ticket c8185c47) : nouveaux ou modifiés depuis la revue.
  const changedFilter = params.get('fchanged') === '1'
  const fvis = params.get('fvis') ?? ''
  const visFilter = useMemo(() => fvis.split(',').filter(Boolean), [fvis])
  // Sources de la recherche : absent = la source ouverte (rien ne change tant
  // qu'il n'y en a qu'une), « all » = toutes, sinon une liste.
  const fsrc = params.get('fsrc')
  // La source n'est pas un critère : seule, elle ne lance pas de recherche.
  const criteriaActive =
    !!fText ||
    !!kindFilter ||
    !!badgeFilter ||
    !!tagFilter ||
    visFilter.length > 0 ||
    changedFilter
  const [search, setSearch] = useState(fText)
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({
    key: 'name',
    asc: true,
  })
  const [treeWidth, setTreeWidth] = useState(storedWidth)
  const [previewOpen, setPreviewOpen] = useState(storedPreviewOpen)
  const [hideExcluded, setHideExcluded] = useState(storedHideExcluded)
  const toggleHideExcluded = (hide: boolean) => {
    setHideExcluded(hide)
    try {
      localStorage.setItem(HIDE_EXCLUDED_KEY, hide ? 'yes' : 'no')
    } catch {
      // stockage indisponible : le choix vaut pour cette visite
    }
  }
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
  const view: View =
    viewParam === 'list' || viewParam === 'graph' || viewParam === 'application' ? viewParam : 'tiles'
  // Vue « Application » (cartographie applicative) : le module sélectionné.
  const moduleParam = params.get('module') ?? ''
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
      : view === 'application'
        ? { view: null, module: null }
        : {}
  /** Navigation vers un dossier : quitte le graphe s'il est affiché. */
  // Pendant une recherche, naviguer dans l'arbre ou le fil d'Ariane déplace la
  // portée (`in`) sur le nouveau dossier ; ouvrir un résultat, non (sinon la
  // liste se réduirait au dossier du résultat cliqué).
  const goFolder = (changes: Record<string, string | null>, replace = false) =>
    go(
      {
        ...changes,
        ...leaveGraph(),
        ...(criteriaActive && 'path' in changes ? { in: changes.path ?? null } : {}),
      },
      replace,
    )
  /** Change des critères ; la première recherche lancée d'un dossier y est bornée. */
  const setCriteria = (changes: Record<string, string | null>) =>
    go({ ...changes, ...(criteriaActive ? {} : { in: path || null }) }, true)
  const clearCriteria = () => {
    setSearch('')
    go(
      { q: null, fkind: null, fstate: null, ftag: null, fvis: null, fchanged: null, in: null },
      true,
    )
  }
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
    queryKey: ['rawTree', spaceId, branchId, sourceSlug, mode, familyFilter],
    queryFn: () => api.getRawExplorerTree(spaceId, branchId, sourceSlug, mode, familyFilter),
    // L'arbre reste affiché pendant qu'il se recalcule pour un autre type.
    placeholderData: (previous) => previous,
    enabled: !!current && current.collected,
    staleTime: STALE_MS,
    retry: false,
  })
  // Sans objet module, la vue « Application » est grisée.
  const hasModules = (tree.data?.modules ?? []).length > 0

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

  // Saisie : écrite dans l'adresse après une courte pause ; un retour arrière
  // (adresse changée) remet le champ à jour.
  useEffect(() => {
    const value = search.trim()
    if (value === fText) return
    const id = setTimeout(() => setCriteria({ q: value || null }), 300)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])
  useEffect(() => {
    if (search.trim() !== fText) setSearch(fText)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fText])

  const collectedSlugs = useMemo(
    () => sourceList.filter((s) => s.collected).map((s) => s.slug),
    [sourceList],
  )
  const filterSources = useMemo(
    () =>
      fsrc === 'all'
        ? collectedSlugs
        : fsrc
          ? fsrc.split(',').filter((slug) => sourceList.some((s) => s.slug === slug))
          : [sourceSlug],
    [fsrc, collectedSlugs, sourceList, sourceSlug],
  )
  const singleSource = filterSources.length === 1 && filterSources[0] === sourceSlug
  const scopeIn = params.get('in') ?? ''
  // Portée : pendant une recherche, le dossier où elle a été lancée (`in`,
  // absent = toute la source) ; sinon le dossier ouvert, pour que les filtres
  // proposent ce qu'il contient, sous-dossiers compris (cas de `src`).
  const scopePath = singleSource ? (criteriaActive ? scopeIn : path) : ''
  const results = useQuery({
    queryKey: [
      'rawSearch',
      spaceId,
      branchId,
      mode,
      filterSources,
      scopePath,
      fText,
      tagFilter,
      fvis,
      kindFilter,
      badgeFilter,
      changedFilter,
      familyFilter,
      criteriaActive && hideExcluded,
    ],
    queryFn: () =>
      api.searchRawExplorer(spaceId, branchId, {
        mode,
        sources: filterSources,
        path: scopePath,
        q: fText,
        tag: tagFilter,
        visibilities: visFilter,
        kind: kindFilter,
        badge: badgeFilter,
        changed: changedFilter,
        family: familyFilter,
        hideExcluded: criteriaActive && hideExcluded,
      }),
    enabled: !!current && current.collected && filterSources.length > 0,
    // Les listes de filtres restent en place pendant le rechargement.
    placeholderData: (previous) => previous,
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

  // Recherche en cours : la grille montre les objets trouvés dans toute la
  // source (et non plus le dossier), les autres filtres s'appliquant dessus.
  const searching = criteriaActive
  const objects: RawObjectItem[] = useMemo(
    () => (searching ? (results.data?.results ?? []) : (folder.data?.objects ?? [])),
    [searching, results.data, folder.data],
  )
  const resultFolder = useMemo(
    () =>
      new Map((results.data?.results ?? []).map((r) => [`${r.source}/${r.file}`, r.folder])),
    [results.data],
  )
  // Le dossier courant reste visible dans l'arbre (après un clic sur un
  // résultat de recherche, il peut être loin dans une longue arborescence).
  useEffect(() => {
    document
      .querySelector('.raw-tree .raw-tree-node.active')
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [path, sourceSlug])
  /** Sélection d'un objet ; pour un résultat de recherche, son dossier s'ouvre dans l'arbre. */
  const selectObject = (file: string, source?: string) => {
    const key = `${source ?? sourceSlug}/${file}`
    if (searching && resultFolder.has(key)) {
      go({ source: source ?? sourceSlug, path: resultFolder.get(key) || null, sel: file })
    } else go({ sel: file }, true)
  }
  // Arbre filtré (complément du ticket be0f301c) : pendant une recherche, les
  // dossiers d'une source cherchée ne gardent que ceux qui contiennent un objet
  // correspondant (sous-dossiers compris), avec ce nombre en compteur. Les
  // compteurs portent sur toute la source, même bornée par la puce « dans … » :
  // l'arbre dit où aller.
  const treeCounts = searching ? results.data?.tree_counts : undefined
  const sourceCounts = treeCounts?.[sourceSlug]
  // Filtre « Type » (bug d307159a) : le serveur compte le seul type choisi ;
  // les dossiers qui n'en contiennent pas disparaissent (la racine reste).
  const treeFolders = useMemo(
    () =>
      sourceCounts
        ? (tree.data?.folders ?? [])
            .filter((f) => (sourceCounts[f.path] ?? 0) > 0)
            .map((f) => ({ ...f, files: sourceCounts[f.path] }))
        : (tree.data?.folders ?? []).filter((f) => !familyFilter || f.path === '' || f.files > 0),
    [tree.data, sourceCounts, familyFilter],
  )
  // Valeurs proposées par les filtres : celles de la portée (sources, dossier et
  // sous-dossiers), rendues par le serveur. Un filtre sans valeur ne s'affiche
  // pas ; une valeur choisie reste proposée même si la portée change.
  const facets = results.data?.facets
  const withChosen = (values: string[], chosen: string[]) => [
    ...values,
    ...chosen.filter((c) => c && !values.includes(c)),
  ]
  const kinds = withChosen(facets?.kinds ?? [], [kindFilter]).sort()
  const presentVisibilities = VISIBILITIES.filter((v) =>
    withChosen(facets?.visibilities ?? [], visFilter).includes(v),
  )
  const presentBadges = BADGES.filter((b) =>
    withChosen(facets?.badges ?? [], [badgeFilter]).includes(b),
  )
  const presentTags = withChosen(facets?.tags ?? [], [tagFilter]).sort()
  // Pendant une recherche, le serveur a déjà appliqué tous les critères.
  // Un contrat OpenAPI vient d'un parseur openapi (comme côté serveur, object_family).
  const familyOf = (o: RawObjectItem) => ((o.parser ?? '').startsWith('openapi') ? 'openapi' : 'schema')
  const shownObjects = searching
    ? objects
    : objects.filter(
        (o) =>
          !(hideExcluded && o.badges.includes('excluded')) &&
          (!familyFilter || familyOf(o) === familyFilter),
      )
  const shownFolders = searching ? [] : (folder.data?.folders ?? [])
  const shownDocuments =
    searching || (familyFilter && familyFilter !== 'markdown') ? [] : (folder.data?.documents ?? [])
  // Contrat OpenAPI complet (ticket e79d008f) : de la famille « Contrats OpenAPI ».
  const shownContracts =
    searching || (familyFilter && familyFilter !== 'openapi') ? [] : (folder.data?.contracts ?? [])
  const hasContent = searching ? !!results.data : !!folder.data

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
    if (isContractSelection(opened)) {
      return (
        <RawContractPage
          spaceId={spaceId}
          branchId={branchId}
          slug={sourceSlug}
          path={opened}
          // Pas de sélection au retour : l'aperçu latéral ne montre que les
          // objets et les documents.
          onBack={() => go({ open: null, sel: null })}
          onOpenObject={(file) => go({ open: file, sel: file })}
        />
      )
    }
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
                  <span className="raw-count">
                    {treeCounts?.[s.slug]
                      ? (treeCounts[s.slug][''] ?? 0)
                      : familyFilter && s.slug === sourceSlug && tree.data
                        ? // Filtre « Type » : total du seul type choisi (racine de l'arbre).
                          (tree.data.folders.find((f) => f.path === '')?.files ?? 0)
                        : s.objects + s.documents}
                  </span>
                </button>
                {s.slug === sourceSlug && tree.data ? (
                  <FolderBranch
                    folders={treeFolders}
                    parent=""
                    current={path}
                    label={folderLabel}
                    onOpen={(p) => goFolder({ path: p, sel: null })}
                    excludedBy={excludedBy}
                    modules={tree.data?.modules ?? []}
                    selectedModule={view === 'application' ? moduleParam : ''}
                    onOpenModule={(m) =>
                      go({ view: 'application', module: m.file, path: m.folder || null, sel: null, back: null, center: null })
                    }
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
          {/* Fil d'Ariane, vues et filtres restent visibles quand la grille défile. */}
          <div className="raw-head">
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
              <button
                type="button"
                className={`btn btn-sm${view === 'application' ? ' btn-primary' : ''}`}
                aria-pressed={view === 'application'}
                disabled={!hasModules}
                title={hasModules ? undefined : t('rawAppNone')}
                onClick={() => go({ view: 'application', back: null, center: null }, true)}
              >
                {t('rawViewApplication')}
              </button>
            </div>
          </div>

          {current && current.collected ? (
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
                </div>
                <SourceFilter
                  sources={sourceList.filter((s) => s.collected)}
                  chosen={filterSources}
                  current={sourceSlug}
                  onChange={(next) => go({ fsrc: next }, true)}
                />
                <select
                  className="select"
                  aria-label={t('rawFilterFamily')}
                  value={familyFilter}
                  onChange={(e) => go({ ffamily: e.target.value || null }, true)}
                >
                  <option value="">{t('rawFilterFamilyAll')}</option>
                  <option value="schema">{t('rawFamily.schema')}</option>
                  <option value="markdown">{t('rawFamily.markdown')}</option>
                  <option value="openapi">{t('rawFamily.openapi')}</option>
                </select>
                {kinds.length > 1 ? (
                  <select
                    className="select"
                    aria-label={t('rawFilterKind')}
                    value={kindFilter}
                    onChange={(e) => setCriteria({ fkind: e.target.value || null })}
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
                    onChange={(e) => setCriteria({ fstate: e.target.value || null })}
                  >
                    <option value="">{t('rawFilterStateAll')}</option>
                    {presentBadges.map((b) => (
                      <option key={b} value={b}>
                        {t(`rawBadge.${b}`)}
                      </option>
                    ))}
                  </select>
                ) : null}
                {presentBadges.includes('excluded') || hideExcluded ? (
                  <label className="raw-hide-excluded">
                    <input
                      type="checkbox"
                      checked={hideExcluded}
                      onChange={(e) => toggleHideExcluded(e.target.checked)}
                    />{' '}
                    {t('rawHideExcluded')}
                  </label>
                ) : null}
                {presentTags.length > 0 ? (
                  <select
                    className="select"
                    aria-label={t('rawFilterTag')}
                    value={tagFilter}
                    onChange={(e) => setCriteria({ ftag: e.target.value || null })}
                  >
                    <option value="">{t('rawFilterTagAll')}</option>
                    {presentTags.map((tag) => (
                      <option key={tag} value={tag}>
                        {tag}
                      </option>
                    ))}
                  </select>
                ) : null}
                {changedFilter ||
                (facets?.badges ?? []).some((b) => b === 'new' || b === 'modified') ? (
                  <label className="raw-hide-excluded">
                    <input
                      type="checkbox"
                      checked={changedFilter}
                      onChange={(e) => setCriteria({ fchanged: e.target.checked ? '1' : null })}
                    />{' '}
                    {t('rawFilterChanged')}
                  </label>
                ) : null}
                {presentVisibilities.length > 0 ? (
                  <fieldset className="raw-vis-filter">
                    <legend>{t('rawFilterVisibility')}</legend>
                    {presentVisibilities.map((v) => (
                      <label key={v}>
                        <input
                          type="checkbox"
                          checked={visFilter.includes(v)}
                          onChange={(e) => {
                            const next = e.target.checked
                              ? [...visFilter, v]
                              : visFilter.filter((x) => x !== v)
                            setCriteria({ fvis: next.join(',') || null })
                          }}
                        />{' '}
                        {t(`rawVisibility.${v}`)}
                      </label>
                    ))}
                  </fieldset>
                ) : null}
              </div>
          ) : null}
          </div>

          {current && current.collected ? (
            <>
              {searching ? (
                <p className="raw-search-summary" role="status">
                  {!results.data ? (
                    <span className="spinner" aria-hidden="true" />
                  ) : (
                    t(results.data.truncated ? 'rawCriteriaSummaryLimit' : 'rawCriteriaSummary', {
                      count: results.data.results.length,
                    })
                  )}{' '}
                  {singleSource && scopeIn ? (
                    <span className="raw-scope-chip" title={scopeIn}>
                      {t('rawScopeIn', { folder: folderLabel(scopeIn) })}
                      <button
                        type="button"
                        className="btn-link"
                        aria-label={t('rawScopeWiden')}
                        title={t('rawScopeWiden')}
                        onClick={() => go({ in: null }, true)}
                      >
                        {'✕'}
                      </button>
                    </span>
                  ) : !singleSource ? (
                    <span className="raw-scope-chip">
                      {t('rawScopeSources', { count: filterSources.length })}
                    </span>
                  ) : null}{' '}
                  <button type="button" className="btn-link" onClick={clearCriteria}>
                    {t('rawCriteriaClear')}
                  </button>
                </p>
              ) : null}
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
          ) : view === 'application' ? (
            hasModules ? (
              <RawApplication
                spaceId={spaceId}
                branchId={branchId}
                slug={sourceSlug}
                selected={moduleParam}
                onSelect={(m) => go({ module: m.file }, true)}
                onOpenClasses={(m) => go({ view: null, module: null, path: m.folder || null, sel: null })}
              />
            ) : (
              <div className="empty-note">{t('rawAppNone')}</div>
            )
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
          ) : folder.isLoading && !searching ? (
            <div className="panel" aria-busy="true">
              <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
            </div>
          ) : hasContent &&
            shownFolders.length +
              shownObjects.length +
              shownDocuments.length +
              shownContracts.length ===
              0 ? (
            <div className="empty-note">
              {searching ? t('rawSearchNone') : t('rawFolderEmpty')}
            </div>
          ) : hasContent && view === 'tiles' ? (
            <Tiles
              folders={shownFolders}
              objects={shownObjects}
              documents={shownDocuments}
              contracts={shownContracts}
              selected={selected}
              currentSource={sourceSlug}
              showSource={searching && !singleSource}
              sourceLabel={(slug) => sourceList.find((x) => x.slug === slug)?.label ?? slug}
              folderLabel={folderLabel}
              onOpenFolder={(p) => go({ path: p, sel: null })}
              onSelect={selectObject}
              onOpenItem={(file) => go({ open: file, sel: file })}
              excludedBy={excludedBy}
              refMenu={(o) => (
                <RefMenu
                  spaceId={spaceId}
                  branchId={branchId}
                  slug={o.source ?? sourceSlug}
                  mode={mode}
                  file={o.file}
                  count={o.referenced_by ?? 0}
                  onOpen={openTarget}
                />
              )}
            />
          ) : hasContent ? (
            <ListView
              folders={shownFolders}
              objects={shownObjects}
              documents={shownDocuments}
              contracts={shownContracts}
              selected={selected}
              currentSource={sourceSlug}
              showSource={searching && !singleSource}
              sourceLabel={(slug) => sourceList.find((x) => x.slug === slug)?.label ?? slug}
              sort={sort}
              onSort={(key) => setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))}
              folderLabel={folderLabel}
              onOpenFolder={(p) => go({ path: p, sel: null })}
              onSelect={selectObject}
              onOpenItem={(file) => go({ open: file, sel: file })}
              refMenu={(o) => (
                <RefMenu
                  spaceId={spaceId}
                  branchId={branchId}
                  slug={o.source ?? sourceSlug}
                  mode={mode}
                  file={o.file}
                  count={o.referenced_by ?? 0}
                  onOpen={openTarget}
                />
              )}
            />
          ) : null}
          {path && folder.data && !searching ? (
            <button
              type="button"
              className="btn btn-sm raw-up"
              onClick={() => goFolder({ path: parentOf(path) || null, sel: null })}
            >
              {t('rawUp')}
            </button>
          ) : null}
        </section>
        {previewOpen && current?.collected && view === 'application' ? (
          <RawModulePreview
            spaceId={spaceId}
            branchId={branchId}
            slug={sourceSlug}
            file={moduleParam}
            onCollapse={() => togglePreview(false)}
          />
        ) : previewOpen && current?.collected ? (
          <RawPreview
            spaceId={spaceId}
            branchId={branchId}
            slug={sourceSlug}
            mode={mode}
            selected={selected}
            folderObjects={objects.filter((o) => (o.source ?? sourceSlug) === sourceSlug)}
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
      // Exclure retire aussitôt les objets du répertoire de mon brouillon
      // (ticket docflow 64e7b42f) : arbre, compteurs et barre du brouillon suivent.
      await refreshRawQueries(queryClient)
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
  modules = [],
  selectedModule = '',
  onOpenModule,
}: {
  folders: RawFolderItem[]
  parent: string
  current: string
  label: (path: string) => string
  onOpen: (path: string) => void
  excludedBy: (path: string) => string | null
  /** Objets module (cartographie applicative), rangés sous leur dossier. */
  modules?: RawTreeModule[]
  selectedModule?: string
  onOpenModule?: (module: RawTreeModule) => void
}) {
  const { t } = useTranslation()
  const children = folders.filter((f) => f.path !== '' && parentOf(f.path) === parent)
  const here = modules.filter((m) => m.folder === parent)
  if (children.length === 0 && here.length === 0) return null
  return (
    <ul role="group" className="raw-tree-list">
      {here.map((m) => (
        // Gael : « sur le menu de gauche c'est en cliquant sur un objet module que je
        // tombe sur cette vue. »
        <li key={m.file} role="treeitem">
          <button
            type="button"
            className={`raw-tree-node raw-tree-module${selectedModule === m.file ? ' active' : ''}`}
            onClick={() => onOpenModule?.(m)}
            title={t('rawAppOpenModule')}
          >
            <span className="raw-glyph-sm">MOD</span> {m.title}
            <span className="raw-count">{t('rawAppModuleLabel')}</span>
          </button>
        </li>
      ))}
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
                modules={modules}
                selectedModule={selectedModule}
                onOpenModule={onOpenModule}
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

function Badges({ obj, withTags = true }: { obj: RawObjectItem; withTags?: boolean }) {
  const { t } = useTranslation()
  const tags = withTags ? obj.tags : []
  if (obj.badges.length === 0 && !obj.visibility && tags.length === 0) return null
  return (
    <span className="raw-badges">
      <VisibilityBadge visibility={obj.visibility} source={obj.visibility_source} />
      <TagBadges tags={tags} />
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
  contracts?: RawContractItem[]
  selected: string
  /** Source ouverte : la sélection (`sel`) ne porte que sur elle. */
  currentSource: string
  /** Résultats de plusieurs sources : chacun dit la sienne. */
  showSource: boolean
  sourceLabel: (slug: string) => string
  folderLabel: (path: string) => string
  onOpenFolder: (path: string) => void
  onSelect: (file: string, source?: string) => void
  excludedBy?: (path: string) => string | null
  /** Double-clic : vue exclusive d'un objet, ou document en pleine page. */
  onOpenItem?: (file: string) => void
  /** Compteur « référencé par » d'un objet, avec son menu déroulant. */
  refMenu?: (obj: RawObjectItem) => ReactNode
}

function Tiles({
  folders,
  objects,
  documents,
  contracts = [],
  selected,
  currentSource,
  showSource,
  sourceLabel,
  folderLabel,
  onOpenFolder,
  onSelect,
  excludedBy,
  onOpenItem,
  refMenu,
}: ContentProps) {
  const { t } = useTranslation()
  const isSelected = (o: RawObjectItem) =>
    selected === o.file && (o.source ?? currentSource) === currentSource
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
      {contracts.length > 0 ? (
        <section>
          <h2 className="panel-label">{t('rawSectionContracts')}</h2>
          <div className="raw-tiles">
            {contracts.map((c) => (
              // Un clic ouvre la vue lisible, comme un document.
              <button
                key={c.path}
                type="button"
                className="raw-tile raw-tile-contract"
                onClick={() => onOpenItem?.(c.path)}
              >
                <span className="raw-glyph raw-glyph-oas">OAS</span>
                {/* Le nom en dur est le repère (Gael : « pour comprendre que c'est
                    un répertoire qui expose un contrat OAS ») : toujours en entier ;
                    le radical, quand le dossier a plusieurs contrats, en dessous. */}
                <span className="raw-tile-name mono">{FULL_CONTRACT_NAME}</span>
                {lastSegment(c.path) !== FULL_CONTRACT_NAME ? (
                  <span className="raw-tile-sub mono">
                    {lastSegment(c.path).slice(0, -FULL_CONTRACT_NAME.length - 1)}
                  </span>
                ) : null}
                <span className="raw-tile-sub">
                  {[c.title, c.version].filter(Boolean).join(' · ')}
                </span>
                <span className="raw-tile-sub">
                  {t('rawContractTileSub', { count: c.operations })}
                </span>
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
              // Le compteur est à côté du bouton de la vignette, pas dedans : un
              // bouton ne peut pas en contenir d'autres (menu cliquable).
              <div key={`${o.source ?? ''}/${o.file}`} className="raw-tile-wrap">
                <button
                  type="button"
                  className={`raw-tile${isSelected(o) ? ' selected' : ''}`}
                  aria-pressed={isSelected(o)}
                  onClick={() => onSelect(o.file, o.source)}
                  onDoubleClick={() => onOpenItem?.(o.file)}
                >
                  <span className="raw-glyph">{kindGlyph(o.kind)}</span>
                  <span className="raw-tile-name">{o.title}</span>
                  {showSource && o.source ? (
                    <span className="raw-tile-source">{sourceLabel(o.source)}</span>
                  ) : null}
                  <span className="raw-tile-sub">
                    {t('rawFields', { count: o.fields })}
                    {o.origin ? ` · ${lastSegment(o.origin)}` : ''}
                  </span>
                  <Badges obj={o} withTags={false} />
                  {o.tags.length > 0 ? (
                    // Au repos : les deux premiers tags et « +N » ; au survol
                    // (effet loupe), tous les tags et le namespace.
                    <span className="raw-badges raw-tile-tags">
                      <TagBadges tags={o.tags.slice(0, TILE_TAGS)} />
                      {o.tags.length > TILE_TAGS ? (
                        <span className="raw-tile-more-count">+{o.tags.length - TILE_TAGS}</span>
                      ) : null}
                    </span>
                  ) : null}
                  <span className="raw-tile-more">
                    {o.tags.length > TILE_TAGS ? (
                      <span className="raw-badges">
                        <TagBadges tags={o.tags.slice(TILE_TAGS)} />
                      </span>
                    ) : null}
                    {o.namespace ? (
                      <span className="mono small raw-tile-path">{o.namespace}</span>
                    ) : null}
                    {o.parser ? (
                      <span className="small muted">{t('rawTileParser', { parser: o.parser })}</span>
                    ) : null}
                  </span>
                </button>
                {refMenu?.(o)}
              </div>
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
                // Un clic ouvre le document (ticket 1bd5ae5b, Gael : « un seul
                // clic suffit ») ; les objets gardent clic = aperçu, double-clic = vue.
                onClick={() => (onOpenItem ? onOpenItem(d.path) : onSelect(d.path))}
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
  contracts = [],
  selected,
  currentSource,
  showSource,
  sourceLabel,
  sort,
  onSort,
  folderLabel,
  onOpenFolder,
  onSelect,
  onOpenItem,
  refMenu,
}: ContentProps & {
  sort: { key: SortKey; asc: boolean }
  onSort: (key: SortKey) => void
}) {
  const { t } = useTranslation()
  const isSelected = (o: RawObjectItem) =>
    selected === o.file && (o.source ?? currentSource) === currentSource
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
            <th>{t('rawColReferencedBy')}</th>
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
              <td />
            </tr>
          ))}
          {sorted.map((o) => (
            <tr
              key={`${o.source ?? ''}/${o.file}`}
              className={`row-clickable${isSelected(o) ? ' selected' : ''}`}
              onClick={() => onSelect(o.file, o.source)}
              onDoubleClick={() => onOpenItem?.(o.file)}
            >
              <td>
                <span className="raw-glyph-sm">{kindGlyph(o.kind)}</span> {o.title}
                {showSource && o.source ? (
                  <span className="raw-tile-source"> · {sourceLabel(o.source)}</span>
                ) : null}
              </td>
              <td>{t(`rawKind.${o.kind}`, { defaultValue: o.kind })}</td>
              <td>{o.fields}</td>
              <td className="mono">{o.origin}</td>
              <td>
                <Badges obj={o} />
              </td>
              <td>{refMenu?.(o)}</td>
            </tr>
          ))}
          {contracts.map((c) => (
            <tr
              key={`oas:${c.path}`}
              className="row-clickable"
              onClick={() => onOpenItem?.(c.path)}
            >
              <td>
                <span className="raw-glyph-sm raw-glyph-oas">OAS</span>{' '}
                <span className="mono">{lastSegment(c.path)}</span>
              </td>
              <td className="muted">{t('rawKindContract')}</td>
              <td className="muted">{t('rawContractTileSub', { count: c.operations })}</td>
              <td className="mono">{c.source}</td>
              <td />
              <td />
            </tr>
          ))}
          {documents.map((d) => (
            <tr
              key={`doc:${d.path}`}
              className={`row-clickable${selected === d.path ? ' selected' : ''}`}
              onClick={() => (onOpenItem ? onOpenItem(d.path) : onSelect(d.path))}
            >
              <td>
                <span className="raw-glyph-sm raw-glyph-doc">DOC</span> {d.title}
              </td>
              <td className="muted">{t('rawKindDocument')}</td>
              <td className="muted">{t('rawSections', { count: d.sections })}</td>
              <td className="mono">{d.path}</td>
              <td />
              <td />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Filtre Source (ticket docflow be0f301c) : sur quelles sources porte la
 * recherche. `null` = la source ouverte (défaut), « all » = toutes, sinon une
 * liste séparée par des virgules. Au moins une source reste cochée.
 */
function SourceFilter({
  sources,
  chosen,
  current,
  onChange,
}: {
  sources: RawExplorerSource[]
  chosen: string[]
  current: string
  onChange: (next: string | null) => void
}) {
  const { t } = useTranslation()
  if (sources.length === 0) return null
  const all = sources.map((s) => s.slug)
  const encode = (list: string[]): string | null =>
    list.length === 1 && list[0] === current
      ? null
      : list.length === all.length && all.every((s) => list.includes(s))
        ? 'all'
        : list.join(',')
  const summary =
    chosen.length === all.length && all.length > 1
      ? t('rawFilterSourceAll')
      : chosen.length === 1
        ? (sources.find((s) => s.slug === chosen[0])?.label ?? chosen[0])
        : t('rawFilterSourceCount', { count: chosen.length })
  return (
    <details className="raw-source-filter">
      <summary aria-label={t('rawFilterSource')}>
        <span className="muted">{t('rawFilterSource')} :</span> {summary}
      </summary>
      <div className="raw-source-filter-menu">
        {all.length > 1 ? (
          <label>
            <input
              type="checkbox"
              checked={chosen.length === all.length}
              onChange={(e) => onChange(e.target.checked ? 'all' : null)}
            />{' '}
            {t('rawFilterSourceAll')}
          </label>
        ) : null}
        {sources.map((s) => {
          const checked = chosen.includes(s.slug)
          return (
            <label key={s.slug}>
              <input
                type="checkbox"
                checked={checked}
                disabled={checked && chosen.length === 1}
                onChange={(e) =>
                  onChange(
                    encode(
                      e.target.checked
                        ? all.filter((x) => x === s.slug || chosen.includes(x))
                        : chosen.filter((x) => x !== s.slug),
                    ),
                  )
                }
              />{' '}
              {s.label}
            </label>
          )
        })}
      </div>
    </details>
  )
}
