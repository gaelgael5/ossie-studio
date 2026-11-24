import { type ReactNode, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type {
  RawContractDetail,
  RawContractObject,
  RawContractOperation,
  RawContractSchema,
} from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { CodeViewer } from '../components/CodeViewer'

const STALE_MS = 30_000

/** Nom en dur de la copie intégrale d'un contrat (ticket e79d008f). */
export const FULL_CONTRACT_NAME = 'contrat.full.openapi.yaml'

export function isContractSelection(sel: string): boolean {
  return sel.endsWith(FULL_CONTRACT_NAME)
}

// Gael : « un affichage que même un enfant comprendrait ». Chaque méthode HTTP
// devient un verbe de tous les jours, avec sa couleur ; le mot technique reste
// lisible à côté pour qui le connaît.
const VERBS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const
const verbKey = (method: string) =>
  (VERBS as readonly string[]).includes(method) ? method.toLowerCase() : 'other'

/** Famille d'un code de réponse : ce que l'écran en dit en un mot. */
function responseKey(code: string): string {
  if (code === 'default') return 'default'
  if (
    ['200', '201', '202', '204', '400', '401', '403', '404', '409', '422', '429', '500', '503'].includes(code)
  )
    return code
  return `${code.charAt(0)}xx`
}

function responseTone(code: string): string {
  if (code.startsWith('2')) return 'ok'
  if (code.startsWith('4')) return 'warn'
  if (code.startsWith('5')) return 'bad'
  return 'neutral'
}

export function RawContractPage({
  spaceId,
  branchId,
  slug,
  path,
  onBack,
  onOpenObject,
}: {
  spaceId: string
  branchId: string
  slug: string
  path: string
  onBack: () => void
  /** Ouvre un objet découpé (fichier schéma de `raw/`). */
  onOpenObject: (file: string) => void
}) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<'readable' | 'yaml'>('readable')
  const contract = useQuery({
    queryKey: ['rawContract', spaceId, branchId, slug, path],
    queryFn: () => api.getRawContract(spaceId, branchId, slug, path),
    staleTime: STALE_MS,
    retry: false,
  })
  const data = contract.data
  const showYaml = tab === 'yaml' || (data && !data.readable)
  return (
    <div className="raw-exclusive">
      <div className="raw-exclusive-nav">
        <button type="button" className="btn btn-sm" onClick={onBack}>
          {t('rawBackToTiles')}
        </button>
      </div>
      {contract.isLoading ? (
        <p aria-busy="true">
          <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
        </p>
      ) : data ? (
        <section className="panel oas">
          <header className="oas-head">
            <span className="raw-glyph raw-glyph-oas">OAS</span>
            <div>
              <h2>{data.readable?.title || data.title || t('rawContract.untitled')}</h2>
              <div className="mono small muted">
                {path.split('/').pop()} · {t('rawContract.from', { source: data.source })}
              </div>
            </div>
          </header>
          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={!showYaml}
              className={`tab${!showYaml ? ' active' : ''}`}
              disabled={!data.readable}
              onClick={() => setTab('readable')}
            >
              {t('rawContract.tabReadable')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={!!showYaml}
              className={`tab${showYaml ? ' active' : ''}`}
              onClick={() => setTab('yaml')}
            >
              {t('rawContract.tabYaml')}
            </button>
          </div>
          {showYaml ? (
            <>
              {!data.readable ? <p className="muted">{t('rawContract.unreadable')}</p> : null}
              <CodeViewer value={data.yaml} label={data.title || path} maxHeight={760} />
            </>
          ) : (
            <Readable data={data} onOpenObject={onOpenObject} />
          )}
        </section>
      ) : (
        <ErrorList messages={errorMessages(t, contract.error)} />
      )}
    </div>
  )
}

function Readable({ data, onOpenObject }: { data: RawContractDetail; onOpenObject: (file: string) => void }) {
  const { t } = useTranslation()
  const view = data.readable!
  // Ticket 82686dc0 (Gael) : « sur le clic d'une méthode … une vue graph comme
  // pour les vignettes ; au clic sur un objet, les propriétés sur la droite ».
  // La sélection reste dans l'écran (pas dans l'adresse) : c'est une lecture.
  const [opIndex, setOpIndex] = useState(0)
  const [objectName, setObjectName] = useState<string | null>(null)
  // Clic sur le nœud de la méthode : le graphe de son corps remplace la liste
  // des objets (ticket c62f94e9).
  const [showBody, setShowBody] = useState(false)
  const op = view.operations[opIndex] as RawContractOperation | undefined
  const detail = objectName ? view.schemas[objectName] : undefined
  // Rangées par premier tag : c'est ainsi que l'auteur du contrat a groupé ses
  // opérations ; sans tag, une seule liste.
  const groups = new Map<string, { op: RawContractOperation; index: number }[]>()
  view.operations.forEach((o, index) => {
    const key = o.tags[0] ?? ''
    groups.set(key, [...(groups.get(key) ?? []), { op: o, index }])
  })
  const verbsUsed = VERBS.filter((v) => view.operations.some((o) => o.method === v))
  const pick = (name: string) => setObjectName(view.schemas[name] ? name : null)
  return (
    <div className="oas-body">
      <div className="oas-intro">
        <p className="oas-lead">
          {view.operations.length > 0
            ? t('rawContract.lead', { count: view.operations.length })
            : t('rawContract.noOperation')}
        </p>
        {view.description ? <p className="oas-desc">{view.description}</p> : null}
        <dl className="oas-facts">
          {view.version ? (
            <>
              <dt>{t('rawContract.version')}</dt>
              <dd>{view.version}</dd>
            </>
          ) : null}
          {view.servers.length > 0 ? (
            <>
              <dt>{t('rawContract.servers', { count: view.servers.length })}</dt>
              <dd className="mono">{view.servers.join(' · ')}</dd>
            </>
          ) : null}
          <dt>{t('rawContract.objectsCount')}</dt>
          <dd>{view.objects.length}</dd>
        </dl>
        {verbsUsed.length > 0 ? (
          <div className="oas-legend" aria-label={t('rawContract.legend')}>
            {verbsUsed.map((v) => (
              <span key={v} className={`oas-verb oas-verb-${verbKey(v)}`}>
                {t(`rawContract.verb.${verbKey(v)}`)} <span className="oas-verb-http">{v}</span>
              </span>
            ))}
            <span className="small muted">{t('rawContract.legendHint')}</span>
          </div>
        ) : null}
      </div>

      <div className="oas-explore">
        {view.operations.length > 0 ? (
          <nav className="oas-op-list" aria-label={t('rawContract.operationsLabel')}>
            {[...groups.entries()].map(([tag, ops]) => (
              <div key={tag || '-'} className="oas-group">
                {groups.size > 1 || tag ? (
                  <h3 className="panel-label">{tag || t('rawContract.otherGroup')}</h3>
                ) : null}
                {ops.map(({ op: o, index }) => (
                  <button
                    key={`${o.method} ${o.path}`}
                    type="button"
                    className={`oas-op-item oas-op-${verbKey(o.method)}${index === opIndex ? ' selected' : ''}`}
                    aria-pressed={index === opIndex}
                    onClick={() => {
                      setOpIndex(index)
                      setObjectName(null)
                    }}
                  >
                    <span className={`oas-verb oas-verb-${verbKey(o.method)}`}>
                      {t(`rawContract.verb.${verbKey(o.method)}`)}{' '}
                      <span className="oas-verb-http">{o.method}</span>
                    </span>
                    <span className="mono oas-path">{o.path}</span>
                    {o.summary ? <span className="oas-summary small">{o.summary}</span> : null}
                    {o.deprecated ? <span className="pill">{t('rawContract.deprecated')}</span> : null}
                  </button>
                ))}
              </div>
            ))}
          </nav>
        ) : null}

        <div className="oas-main">
          {op ? (
            <>
              {/* Le graphe d'abord : c'est la vue de l'action (Gael) ; le détail en mots suit. */}
              <ContractGraph
                op={op}
                schemas={view.schemas}
                selected={objectName}
                onPick={pick}
                onOpClick={() => setShowBody(true)}
              />
              <Operation op={op} onPick={pick} />
            </>
          ) : null}
          {op && showBody ? (
            <BodyGraph
              op={op}
              schemas={view.schemas}
              selected={objectName}
              onPick={pick}
              onClose={() => setShowBody(false)}
            />
          ) : view.objects.length > 0 ? (
            <section className="oas-group">
              <h3 className="panel-label">{t('rawContract.objectsTitle')}</h3>
              <p className="small muted">{t('rawContract.objectsHint')}</p>
              <div className="oas-objects">
                {view.objects.map((o) => (
                  <button
                    key={o.name}
                    type="button"
                    className={`btn btn-sm${o.name === objectName ? ' btn-primary' : ''}`}
                    onClick={() => pick(o.name)}
                  >
                    {o.name}
                  </button>
                ))}
              </div>
            </section>
          ) : null}
        </div>

        <aside className="panel oas-props" aria-label={t('rawContract.propsLabel')}>
          {detail ? (
            <ObjectPanel detail={detail} onPick={pick} onOpenObject={onOpenObject} />
          ) : (
            <p className="muted">{t('rawContract.propsHint')}</p>
          )}
        </aside>
      </div>
    </div>
  )
}

function Operation({ op, onPick }: { op: RawContractOperation; onPick: (name: string) => void }) {
  const { t } = useTranslation()
  const verb = verbKey(op.method)
  return (
    <div className={`oas-op oas-op-${verb}`}>
      <div className="oas-op-head">
        <span className={`oas-verb oas-verb-${verb}`}>
          {t(`rawContract.verb.${verb}`)} <span className="oas-verb-http">{op.method}</span>
        </span>
        <span className="mono oas-path">{op.path}</span>
        <span className="oas-summary">{op.summary || op.operation_id}</span>
      </div>
      <div className="oas-op-body">
        <p className="oas-sentence">{t(`rawContract.sentence.${verb}`, { path: op.path })}</p>
        {op.description ? <p className="oas-desc">{op.description}</p> : null}
        <div className="oas-columns">
          <div className="oas-col oas-give">
            <h4>{t('rawContract.give')}</h4>
            {op.parameters.length === 0 && !op.body ? (
              <p className="muted">{t('rawContract.giveNothing')}</p>
            ) : (
              <ul>
                {op.parameters.map((p) => (
                  <li key={`${p.in}:${p.name}`}>
                    <strong className="mono">{p.name}</strong>{' '}
                    <span className={`pill ${p.required ? 'oas-required' : ''}`}>
                      {p.required ? t('rawContract.required') : t('rawContract.optional')}
                    </span>{' '}
                    <span className="muted">{t(`rawContract.in.${p.in}`, { defaultValue: p.in })}</span>
                    {p.schema ? (
                      <>
                        {' — '}
                        <SchemaWords schema={p.schema} onPick={onPick} />
                      </>
                    ) : null}
                    {p.description ? <div className="small muted">{p.description}</div> : null}
                  </li>
                ))}
                {op.body ? (
                  <li>
                    <strong>{t('rawContract.body')}</strong>{' '}
                    <span className={`pill ${op.body.required ? 'oas-required' : ''}`}>
                      {op.body.required ? t('rawContract.required') : t('rawContract.optional')}
                    </span>
                    {op.body.schema ? (
                      <>
                        {' — '}
                        <SchemaWords schema={op.body.schema} onPick={onPick} />
                      </>
                    ) : null}
                    {op.body.description ? <div className="small muted">{op.body.description}</div> : null}
                  </li>
                ) : null}
              </ul>
            )}
          </div>
          <div className="oas-col oas-get">
            <h4>{t('rawContract.get')}</h4>
            {op.responses.length === 0 ? (
              <p className="muted">{t('rawContract.getNothing')}</p>
            ) : (
              <ul>
                {op.responses.map((r) => (
                  <li key={r.code}>
                    <span className={`oas-code oas-code-${responseTone(r.code)}`}>
                      {responseWords(t, r.code)}
                      <span className="oas-verb-http">{r.code}</span>
                    </span>
                    {r.schema ? (
                      <>
                        {' '}
                        {t('rawContract.youGet')} <SchemaWords schema={r.schema} onPick={onPick} />
                      </>
                    ) : null}
                    {r.description ? <div className="small muted">{r.description}</div> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        {op.operation_id ? (
          <div className="small muted">
            {t('rawContract.technicalName')} <span className="mono">{op.operation_id}</span>
          </div>
        ) : null}
      </div>
    </div>
  )
}

function responseWords(t: (key: string, opts?: Record<string, unknown>) => string, code: string): string {
  return t(`rawContract.code.${responseKey(code)}`, { defaultValue: t('rawContract.code.other') })
}

/** Objets cités par un schéma, avec leur cardinalité (« n » dans une liste). */
function refsOf(schema: RawContractSchema | null | undefined, card = '1'): { name: string; card: string }[] {
  if (!schema) return []
  switch (schema.kind) {
    case 'ref':
      return [{ name: schema.name, card }]
    case 'list':
      return refsOf(schema.of, 'n')
    case 'oneOf':
    case 'anyOf':
    case 'allOf':
      return schema.options.flatMap((o) => refsOf(o, card))
    default:
      return []
  }
}

// Même dessin que le graphe des vignettes (RawGraph) : colonnes de nœuds SVG,
// liens courbes libellés. À gauche ce qu'on donne, au centre la méthode, à
// droite les réponses puis ce qu'elles renvoient ; chaque objet n'apparaît
// qu'une fois (première place gagnée), ce qui coupe les références circulaires.
const G_NODE_W = 165
const G_NODE_H = 44
const G_COL_GAP = 70
const G_ROW_GAP = 18
const G_PAD = 20
// Au-delà de ce nombre de liens, libellés abrégés (complets au survol ou au clic).
const SHORT_LABELS_FROM = 8
const SHORT_LABEL_CHARS = 9

type GNode = {
  id: string
  level: number
  kind: 'op' | 'response' | 'object'
  title: string
  sub: string
  tone: string
}
type GEdge = { from: string; to: string; label: string }

export interface GraphLabels {
  body: string
  response: (code: string) => string
  fields: (n: number) => string
  /** Nom affiché d'un objet : les objets en ligne (« ~body:0 ») ont un libellé. */
  title: (name: string) => string
}

/** Objets que transporte un corps ou une réponse : l'objet en ligne s'il y en a un. */
function carried(
  content:
    { schema?: RawContractSchema | null; inline?: { key: string; card: string } | null } | null | undefined,
): { name: string; card: string }[] {
  if (!content) return []
  if (content.inline) return [{ name: content.inline.key, card: content.inline.card }]
  return refsOf(content.schema)
}

/** Déplie, niveau par niveau, les objets cités par les propriétés (une seule
 * fois chacun). `dir` : +1 vers la droite, -1 vers la gauche. */
function expand(
  frontier: { name: string; level: number; dir: number }[],
  schemas: Record<string, RawContractObject>,
  depth: number,
  addObject: (name: string, level: number) => boolean,
  edges: GEdge[],
) {
  for (let step = 0; step < depth && frontier.length > 0; step += 1) {
    const next: typeof frontier = []
    for (const f of frontier) {
      for (const prop of schemas[f.name]?.properties ?? []) {
        for (const r of refsOf(prop.schema)) {
          const level = f.level + f.dir
          const added = addObject(r.name, level)
          const a = `obj:${f.name}`
          const b = `obj:${r.name}`
          const label = `${prop.name} · ${r.card}`
          if (a !== b) edges.push(f.dir > 0 ? { from: a, to: b, label } : { from: b, to: a, label })
          if (added) next.push({ name: r.name, level, dir: f.dir })
        }
      }
    }
    frontier = next
  }
}

function objectAdder(
  nodes: Map<string, GNode>,
  schemas: Record<string, RawContractObject>,
  labels: GraphLabels,
) {
  return (name: string, level: number) => {
    const id = `obj:${name}`
    if (nodes.has(id)) return false
    const props = schemas[name]?.properties.length ?? 0
    nodes.set(id, {
      id,
      level,
      kind: 'object',
      title: labels.title(name),
      sub: labels.fields(props),
      tone: '',
    })
    return true
  }
}

export function buildOperationGraph(
  op: RawContractOperation,
  schemas: Record<string, RawContractObject>,
  depth: number,
  labels: GraphLabels,
): { nodes: GNode[]; edges: GEdge[] } {
  const nodes = new Map<string, GNode>()
  const edges: GEdge[] = []
  const addObject = objectAdder(nodes, schemas, labels)
  nodes.set('op', {
    id: 'op',
    level: 0,
    kind: 'op',
    title: op.path,
    sub: op.method,
    tone: verbKey(op.method),
  })
  const frontier: { name: string; level: number; dir: number }[] = []
  for (const r of carried(op.body)) {
    addObject(r.name, -1)
    edges.push({ from: `obj:${r.name}`, to: 'op', label: `${labels.body} · ${r.card}` })
    frontier.push({ name: r.name, level: -1, dir: -1 })
  }
  for (const resp of op.responses) {
    const id = `resp:${resp.code}`
    nodes.set(id, {
      id,
      level: 1,
      kind: 'response',
      title: labels.response(resp.code),
      sub: resp.code,
      tone: responseTone(resp.code),
    })
    edges.push({ from: 'op', to: id, label: '' })
    for (const r of carried(resp)) {
      addObject(r.name, 2)
      edges.push({ from: id, to: `obj:${r.name}`, label: r.card })
      frontier.push({ name: r.name, level: 2, dir: 1 })
    }
  }
  expand(frontier, schemas, depth, addObject, edges)
  return { nodes: [...nodes.values()], edges }
}

/** Graphe du corps d'une action (ticket c62f94e9) : le paquet envoyé à gauche,
 * puis les objets cités par ses propriétés, de gauche à droite. */
export function buildBodyGraph(
  op: RawContractOperation,
  schemas: Record<string, RawContractObject>,
  depth: number,
  labels: GraphLabels,
): { nodes: GNode[]; edges: GEdge[] } {
  const nodes = new Map<string, GNode>()
  const edges: GEdge[] = []
  const addObject = objectAdder(nodes, schemas, labels)
  const roots = carried(op.body)
  for (const r of roots) addObject(r.name, 0)
  expand(
    roots.map((r) => ({ name: r.name, level: 0, dir: 1 })),
    schemas,
    depth,
    addObject,
    edges,
  )
  return { nodes: [...nodes.values()], edges }
}

/** Dessin commun aux deux graphes : colonnes, liens libellés (abrégés quand ils
 * sont nombreux, complets au survol ou au clic), objets cliquables. */
function GraphCanvas({
  nodes,
  edges: rawEdges,
  label,
  selected,
  onPick,
  onOpClick,
}: {
  nodes: GNode[]
  edges: GEdge[]
  label: string
  selected: string | null
  onPick: (name: string) => void
  /** Clic sur le nœud de la méthode : montre le graphe de son corps. */
  onOpClick?: () => void
}) {
  const { t } = useTranslation()
  // Gael : « quand il y a beaucoup de liens, afficher juste le début et le
  // reste au clic ou au survol ». Survol : temporaire ; clic : épinglé.
  const [hovered, setHovered] = useState<string | null>(null)
  const [pinned, setPinned] = useState<Set<string>>(new Set())
  const layout = useMemo(() => {
    // Plusieurs liens entre les deux mêmes objets (sendingDepot, recipientDepot) :
    // un seul trait, libellés réunis — comme le graphe des vignettes.
    const merged = new Map<string, GEdge>()
    for (const e of rawEdges) {
      const key = `${e.from}→${e.to}`
      const prev = merged.get(key)
      merged.set(key, prev ? { ...prev, label: [prev.label, e.label].filter(Boolean).join(', ') } : e)
    }
    const edges = [...merged.values()]
    const levels = [...new Set(nodes.map((n) => n.level))].sort((a, b) => a - b)
    const columns = new Map<number, GNode[]>()
    for (const n of nodes) columns.set(n.level, [...(columns.get(n.level) ?? []), n])
    const tallest = Math.max(1, ...[...columns.values()].map((c) => c.length))
    const height = G_PAD * 2 + tallest * (G_NODE_H + G_ROW_GAP) - G_ROW_GAP
    const pos = new Map<string, { x: number; y: number }>()
    levels.forEach((level, col) => {
      const column = columns.get(level) ?? []
      const columnHeight = column.length * (G_NODE_H + G_ROW_GAP) - G_ROW_GAP
      const top = (height - columnHeight) / 2
      column.forEach((n, i) =>
        pos.set(n.id, { x: G_PAD + col * (G_NODE_W + G_COL_GAP), y: top + i * (G_NODE_H + G_ROW_GAP) }),
      )
    })
    const width = Math.max(
      G_NODE_W + G_PAD * 2,
      G_PAD * 2 + levels.length * (G_NODE_W + G_COL_GAP) - G_COL_GAP,
    )
    // Libellé d'un lien posé du côté où il est seul : quand vingt liens
    // convergent vers un objet, leurs libellés au milieu se superposaient.
    const outCount = new Map<string, number>()
    const inCount = new Map<string, number>()
    for (const e of edges) {
      outCount.set(e.from, (outCount.get(e.from) ?? 0) + 1)
      inCount.set(e.to, (inCount.get(e.to) ?? 0) + 1)
    }
    const labelAtSource = (e: GEdge) => (outCount.get(e.from) ?? 0) <= (inCount.get(e.to) ?? 0)
    // Au-delà de quelques liens, les libellés sont abrégés (début seulement).
    const short = edges.length > SHORT_LABELS_FROM
    return { edges, pos, width, height, labelAtSource, short }
  }, [nodes, rawEdges])
  const cut = (text: string, max: number) => (text.length > max ? `${text.slice(0, max - 1)}…` : text)
  const labelOf = (key: string, text: string) =>
    !layout.short || key === hovered || pinned.has(key) ? text : cut(text, SHORT_LABEL_CHARS)
  const togglePin = (key: string) =>
    setPinned((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  return (
    <div className="raw-graph-canvas">
      <svg
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="img"
        aria-label={label}
      >
        <defs>
          <marker
            id="oas-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" className="raw-graph-arrow" />
          </marker>
        </defs>
        {layout.edges.map((e) => {
          const key = `${e.from}→${e.to}`
          const hot = key === hovered || pinned.has(key)
          const a = layout.pos.get(e.from)
          const b = layout.pos.get(e.to)
          if (!a || !b) return null
          const forward = b.x >= a.x
          const x1 = forward ? a.x + G_NODE_W : a.x
          const x2 = forward ? b.x : b.x + G_NODE_W
          const y1 = a.y + G_NODE_H / 2
          const y2 = b.y + G_NODE_H / 2
          const bend = Math.max(30, Math.abs(x2 - x1) / 2) * (forward ? 1 : -1)
          const d = `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`
          return (
            <g
              key={key}
              className={`raw-graph-edge oas-edge${hot ? ' hot' : ''}`}
              onMouseEnter={() => setHovered(key)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => e.label && togglePin(key)}
            >
              {/* Trait invisible et large : un lien fin se survole difficilement. */}
              <path d={d} className="oas-edge-hit" />
              <path d={d} markerEnd="url(#oas-arrow)" />
              {e.label ? (
                layout.labelAtSource(e) ? (
                  <text x={x1 + 6} y={y1 - 5} textAnchor="start">
                    {labelOf(key, e.label)}
                  </text>
                ) : (
                  <text x={x2 - 8} y={y2 - 5} textAnchor="end">
                    {labelOf(key, e.label)}
                  </text>
                )
              ) : null}
              <title>{e.label}</title>
            </g>
          )
        })}
        {nodes.map((n) => {
          const p = layout.pos.get(n.id)
          if (!p) return null
          const isObject = n.kind === 'object'
          const name = n.id.slice(4) // « obj:<nom> »
          const clickable = isObject || (n.kind === 'op' && !!onOpClick)
          return (
            <g
              key={n.id}
              className={`raw-graph-node oas-node oas-node-${n.kind} oas-tone-${n.tone}${
                isObject && name === selected ? ' center' : ''
              }${clickable ? ' oas-node-clickable' : ''}`}
              transform={`translate(${p.x},${p.y})`}
              onClick={isObject ? () => onPick(name) : n.kind === 'op' ? onOpClick : undefined}
              role={clickable ? 'button' : undefined}
              aria-label={clickable ? (isObject ? n.title : t('rawContract.showBody')) : undefined}
            >
              <title>
                {n.kind === 'op' ? `${n.sub} ${n.title} — ${t('rawContract.showBody')}` : n.title}
              </title>
              <rect width={G_NODE_W} height={G_NODE_H} rx={n.kind === 'object' ? 4 : 18} />
              <text x="10" y="19" className="raw-graph-node-title">
                {cut(n.title, 23)}
              </text>
              <text x="10" y="35" className="raw-graph-node-sub">
                {n.kind === 'op' ? t(`rawContract.verb.${n.tone}`) + ' · ' + n.sub : n.sub}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function useGraphLabels(schemas: Record<string, RawContractObject>): GraphLabels {
  const { t } = useTranslation()
  return useMemo(
    () => ({
      body: t('rawContract.graphBody'),
      response: (code: string) => responseWords(t, code),
      fields: (n: number) => t('rawFields', { count: n }),
      title: (name: string) => objectTitle(t, schemas[name], name),
    }),
    [t, schemas],
  )
}

/** Nom affiché d'un objet : son nom, ou « Le paquet envoyé » / « Réponse 200 »
 * pour un corps ou une réponse décrits en ligne. */
function objectTitle(
  t: (key: string, opts?: Record<string, unknown>) => string,
  detail: RawContractObject | undefined,
  name: string,
): string {
  if (detail?.inline === 'body') return t('rawContract.body')
  if (detail?.inline === 'response') return t('rawContract.responseObject', { code: detail.code ?? '' })
  return name
}

function DepthSelect({ value, onChange }: { value: number; onChange: (d: number) => void }) {
  const { t } = useTranslation()
  return (
    <label>
      {t('rawContract.graphDepth')}{' '}
      <select className="select" value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {[0, 1, 2, 3].map((d) => (
          <option key={d} value={d}>
            {d}
          </option>
        ))}
      </select>
    </label>
  )
}

function ContractGraph({
  op,
  schemas,
  selected,
  onPick,
  onOpClick,
}: {
  op: RawContractOperation
  schemas: Record<string, RawContractObject>
  selected: string | null
  onPick: (name: string) => void
  onOpClick: () => void
}) {
  const { t } = useTranslation()
  const [depth, setDepth] = useState(1)
  const labels = useGraphLabels(schemas)
  const graph = useMemo(() => buildOperationGraph(op, schemas, depth, labels), [op, schemas, depth, labels])
  return (
    <section className="oas-graph raw-graph">
      <div className="raw-graph-controls">
        <h3 className="panel-label">{t('rawContract.graphTitle')}</h3>
        <DepthSelect value={depth} onChange={setDepth} />
        <span className="small muted">{t('rawContract.graphHint')}</span>
      </div>
      {op.responses.length === 0 && !op.body ? (
        <p className="empty-note">{t('rawContract.graphEmpty')}</p>
      ) : null}
      <GraphCanvas
        nodes={graph.nodes}
        edges={graph.edges}
        label={t('rawContract.graphLabel', { path: op.path })}
        selected={selected}
        onPick={onPick}
        onOpClick={onOpClick}
      />
    </section>
  )
}

/** Bloc « Le corps de la méthode » (ticket c62f94e9), à la place de la liste
 * des objets, après un clic sur le nœud de la méthode. */
function BodyGraph({
  op,
  schemas,
  selected,
  onPick,
  onClose,
}: {
  op: RawContractOperation
  schemas: Record<string, RawContractObject>
  selected: string | null
  onPick: (name: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [depth, setDepth] = useState(2)
  const labels = useGraphLabels(schemas)
  const graph = useMemo(() => buildBodyGraph(op, schemas, depth, labels), [op, schemas, depth, labels])
  return (
    <section className="oas-graph raw-graph oas-body-graph">
      <div className="raw-graph-controls">
        <h3 className="panel-label">
          {t('rawContract.bodyTitle')} <span className="mono">{`${op.method} ${op.path}`}</span>
        </h3>
        {graph.nodes.length > 0 ? <DepthSelect value={depth} onChange={setDepth} /> : null}
        <button type="button" className="btn btn-sm" onClick={onClose}>
          {t('rawContract.bodyClose')}
        </button>
      </div>
      {graph.nodes.length === 0 ? (
        <p className="empty-note">{op.body ? t('rawContract.bodyNoObject') : t('rawContract.bodyNone')}</p>
      ) : (
        <>
          <p className="small muted">{t('rawContract.bodyHint')}</p>
          <GraphCanvas
            nodes={graph.nodes}
            edges={graph.edges}
            label={t('rawContract.bodyGraphLabel', { path: op.path })}
            selected={selected}
            onPick={onPick}
          />
        </>
      )}
    </section>
  )
}

/** Panneau de droite : les propriétés d'un objet, avec leurs descriptions. */
function ObjectPanel({
  detail,
  onPick,
  onOpenObject,
}: {
  detail: RawContractObject
  onPick: (name: string) => void
  onOpenObject: (file: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="oas-props-body">
      <h3>{objectTitle(t, detail, detail.name)}</h3>
      {detail.description ? (
        <p className="oas-desc">{detail.description}</p>
      ) : (
        <p className="small muted">{t('rawNoDescription')}</p>
      )}
      {detail.extends.length > 0 ? (
        <p className="small">
          {t('rawContract.extends')}{' '}
          {detail.extends.map((e, i) => (
            <span key={e}>
              {i > 0 ? ', ' : ''}
              <button type="button" className="link-button" onClick={() => onPick(e)}>
                {e}
              </button>
            </span>
          ))}
        </p>
      ) : null}
      {detail.properties.length === 0 ? (
        <p className="small">
          {t('rawContract.noProps')} <SchemaWords schema={detail.shape} onPick={onPick} />
        </p>
      ) : (
        <ul className="oas-prop-list">
          {detail.properties.map((p) => (
            <li key={p.name}>
              <div>
                <strong className="mono">{p.name}</strong>{' '}
                <span className={`pill ${p.required ? 'oas-required' : ''}`}>
                  {p.required ? t('rawContract.required') : t('rawContract.optional')}
                </span>
              </div>
              <div className="small">
                <SchemaWords schema={p.schema} onPick={onPick} />
              </div>
              {p.description ? <div className="small muted oas-desc">{p.description}</div> : null}
            </li>
          ))}
        </ul>
      )}
      {detail.file ? (
        <button type="button" className="btn btn-sm" onClick={() => onOpenObject(detail.file)}>
          {t('rawContract.openSplit')}
        </button>
      ) : null}
    </div>
  )
}

/** Un schéma dit en mots : « une liste de Colis », « du texte », « un choix parmi… ». */
function SchemaWords({
  schema,
  onPick,
}: {
  schema: RawContractSchema
  /** Un objet cité : montré dans le panneau des propriétés. */
  onPick: (name: string) => void
}): ReactNode {
  const { t } = useTranslation()
  switch (schema.kind) {
    case 'ref':
      return (
        <>
          {t('rawContract.schema.objectNamed')}{' '}
          <button type="button" className="link-button" onClick={() => onPick(schema.name)}>
            {schema.name}
          </button>
        </>
      )
    case 'list':
      return (
        <>
          {t('rawContract.schema.listOf')} <SchemaWords schema={schema.of} onPick={onPick} />
        </>
      )
    case 'object':
      return schema.fields.length > 0
        ? t('rawContract.schema.objectFields', {
            fields: schema.fields.join(', '),
          })
        : t('rawContract.schema.object')
    case 'oneOf':
    case 'anyOf':
    case 'allOf':
      return (
        <>
          {t(`rawContract.schema.${schema.kind}`)}{' '}
          {schema.options.map((o, i) => (
            <span key={i}>
              {i > 0
                ? ` ${t(schema.kind === 'allOf' ? 'rawContract.schema.and' : 'rawContract.schema.or')} `
                : ''}
              <SchemaWords schema={o} onPick={onPick} />
            </span>
          ))}
        </>
      )
    default: {
      if (schema.enum && schema.enum.length > 0)
        return t('rawContract.schema.choice', {
          values: schema.enum.join(', '),
        })
      const fmt = schema.format ?? ''
      if (fmt === 'date' || fmt === 'date-time') return t(`rawContract.schema.${fmt}`)
      return t(`rawContract.schema.type.${schema.type || 'any'}`, {
        defaultValue: schema.type || t('rawContract.schema.type.any'),
      })
    }
  }
}
