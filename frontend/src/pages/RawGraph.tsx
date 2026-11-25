import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { RawExplorerMode, RawGraphEdge, RawGraphNode } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

// Vue graphe de l'explorateur raw : l'objet au centre, ses références sortantes
// à droite, entrantes à gauche (toutes sources de l'espace). Couche raw : ce
// sont des références LUES dans la source, pas des relations validées — un
// bandeau permanent le rappelle et aucun vocabulaire Ossie n'apparaît ici.
// Dessin SVG maison : quelques dizaines de nœuds en colonnes, pas besoin d'une
// bibliothèque de graphes (et rien à charger de l'extérieur).

const NODE_W = 190
const NODE_H = 46
const COL_GAP = 120
const ROW_GAP = 22
const PAD = 24
// Au-delà, on alerte et on propose de filtrer plutôt que d'afficher un graphe illisible.
const READABLE_NODES = 25

type Category = 'outgoing' | 'incoming' | 'inherits' | 'external'
const CATEGORIES: Category[] = ['outgoing', 'incoming', 'inherits', 'external']

function categoryOf(edge: RawGraphEdge, nodes: Map<string, RawGraphNode>): Category {
  if (nodes.get(edge.to)?.external) return 'external'
  if (edge.kind === 'inherits') return 'inherits'
  return edge.direction
}

interface GraphProps {
  spaceId: string
  branchId: string
  mode: RawExplorerMode
  center: { slug: string; file: string }
  onSelect: (node: RawGraphNode) => void
  onRecenter: (node: RawGraphNode) => void
}

export function RawGraph({ spaceId, branchId, mode, center, onSelect, onRecenter }: GraphProps) {
  const { t } = useTranslation()
  const [depth, setDepth] = useState(1)
  const [shown, setShown] = useState<Record<Category, boolean>>({
    outgoing: true,
    incoming: true,
    inherits: true,
    external: true,
  })
  const [hover, setHover] = useState<RawGraphNode | null>(null)

  const graph = useQuery({
    queryKey: ['rawGraph', spaceId, branchId, mode, center.slug, center.file, depth],
    queryFn: () => api.getRawGraph(spaceId, branchId, center.slug, mode, center.file, depth),
    staleTime: 30_000,
    retry: false,
  })

  const layout = useMemo(() => {
    const data = graph.data
    if (!data) return null
    const byId = new Map(data.nodes.map((n) => [n.id, n]))
    // Filtres sans rechargement : on masque des catégories de liens, puis les
    // nœuds qui ne tiennent plus à rien (le centre reste toujours).
    const edges = data.edges.filter((e) => shown[categoryOf(e, byId)])
    const linked = new Set<string>([data.center])
    for (const e of edges) {
      linked.add(e.from)
      linked.add(e.to)
    }
    const nodes = data.nodes.filter((n) => linked.has(n.id))
    const levels = [...new Set(nodes.map((n) => n.level))].sort((a, b) => a - b)
    const minLevel = levels[0] ?? 0
    const columns = new Map<number, RawGraphNode[]>()
    for (const n of nodes) columns.set(n.level, [...(columns.get(n.level) ?? []), n])
    const tallest = Math.max(1, ...[...columns.values()].map((c) => c.length))
    const height = PAD * 2 + tallest * (NODE_H + ROW_GAP) - ROW_GAP
    const pos = new Map<string, { x: number; y: number }>()
    for (const [level, column] of columns) {
      column.sort((a, b) => a.title.localeCompare(b.title))
      const columnHeight = column.length * (NODE_H + ROW_GAP) - ROW_GAP
      const top = (height - columnHeight) / 2
      column.forEach((n, i) => {
        pos.set(n.id, {
          x: PAD + (level - minLevel) * (NODE_W + COL_GAP),
          y: top + i * (NODE_H + ROW_GAP),
        })
      })
    }
    const width = PAD * 2 + levels.length * (NODE_W + COL_GAP) - COL_GAP
    // Plusieurs champs entre les deux mêmes objets : un seul trait, libellés groupés.
    const grouped = new Map<string, RawGraphEdge[]>()
    for (const e of edges) {
      const key = `${e.from}→${e.to}→${e.kind === 'inherits' ? 'i' : 'r'}`
      grouped.set(key, [...(grouped.get(key) ?? []), e])
    }
    return { nodes, edges, grouped, pos, width: Math.max(width, NODE_W + PAD * 2), height, byId }
  }, [graph.data, shown])

  if (graph.isLoading) {
    return (
      <div className="panel" aria-busy="true">
        <span className="spinner" aria-hidden="true" /> {t('rawLoading')}
      </div>
    )
  }
  if (graph.error || !graph.data || !layout) {
    return <ErrorList messages={errorMessages(t, graph.error)} />
  }
  const data = graph.data

  const edgeLabel = (group: RawGraphEdge[]) =>
    group[0].kind === 'inherits'
      ? t('rawRefKind.inherits')
      : group
          .map((e) => `${e.field} · ${e.cardinality}${e.virtual ? ` · ${t('rawVirtualShort')}` : ''}`)
          .join(', ')

  return (
    <div className="raw-graph">
      <p className="raw-graph-banner">{t('rawGraphBanner')}</p>
      <div className="raw-graph-controls">
        <label>
          {t('rawGraphDepth')}{' '}
          <select
            className="select"
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
          >
            {[1, 2, 3].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        {CATEGORIES.map((c) => (
          <label key={c}>
            <input
              type="checkbox"
              checked={shown[c]}
              onChange={(e) => setShown((s) => ({ ...s, [c]: e.target.checked }))}
            />{' '}
            {t(`rawGraphCategory.${c}`)}
          </label>
        ))}
        <span className="raw-graph-legend">
          <span className="raw-graph-legend-ref">{t('rawGraphLegendRef')}</span>
          <span className="raw-graph-legend-inherits">{t('rawGraphLegendInherits')}</span>
          <span className="raw-graph-legend-external">{t('rawGraphLegendExternal')}</span>
        </span>
      </div>

      {data.truncated || layout.nodes.length > READABLE_NODES ? (
        <p className="raw-graph-alert" role="status">
          {t('rawGraphTooMany', { count: layout.nodes.length })}
        </p>
      ) : null}

      {/* Info-bulle en haut de la zone : elle ne masque ni le nœud ni les libellés. */}
      <div className="raw-graph-tooltip" aria-live="polite">
        {hover ? (
          <>
            <strong>{hover.title}</strong>
            {hover.kind ? (
              <span className="muted">
                {' '}
                · {t(`rawKind.${hover.kind}`, { defaultValue: hover.kind })}
              </span>
            ) : null}
            <div className="small">
              {hover.external
                ? t('rawGraphExternalHint')
                : hover.description || <span className="muted">{t('rawNoDescription')}</span>}
            </div>
            {hover.fields.length > 0 ? (
              <div className="mono small muted">{hover.fields.join(' · ')}</div>
            ) : null}
          </>
        ) : (
          <span className="muted small">{t('rawGraphHoverHint')}</span>
        )}
      </div>

      {data.edges.length === 0 ? <p className="empty-note">{t('rawGraphNoReference')}</p> : null}

      <div className="raw-graph-canvas">
        <svg
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          role="img"
          aria-label={t('rawGraphLabel', { title: data.nodes[0]?.title ?? '' })}
        >
          <defs>
            <marker
              id="raw-arrow"
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
          {[...layout.grouped.entries()].map(([key, group]) => {
            const a = layout.pos.get(group[0].from)
            const b = layout.pos.get(group[0].to)
            if (!a || !b) return null
            const forward = b.x >= a.x
            const x1 = forward ? a.x + NODE_W : a.x
            const x2 = forward ? b.x : b.x + NODE_W
            const y1 = a.y + NODE_H / 2
            const y2 = b.y + NODE_H / 2
            const bend = Math.max(40, Math.abs(x2 - x1) / 2) * (forward ? 1 : -1)
            const d = `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`
            const inherits = group[0].kind === 'inherits'
            // Relation virtuelle (profil de nommage, b5f333b4) : pointillé fin.
            const virtual = group.every((e) => e.virtual)
            return (
              <g
                key={key}
                className={`raw-graph-edge${inherits ? ' inherits' : ''}${virtual ? ' virtual' : ''}`}
              >
                <path d={d} markerEnd="url(#raw-arrow)" />
                <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6} textAnchor="middle">
                  {edgeLabel(group)}
                </text>
              </g>
            )
          })}
          {layout.nodes.map((n) => {
            const p = layout.pos.get(n.id)
            if (!p) return null
            const isCenter = n.id === data.center
            return (
              <g
                key={n.id}
                className={`raw-graph-node${isCenter ? ' center' : ''}${n.external ? ' external' : ''}`}
                transform={`translate(${p.x},${p.y})`}
                onMouseEnter={() => setHover(n)}
                onMouseLeave={() => setHover(null)}
                onClick={() => (n.external ? undefined : onSelect(n))}
                onDoubleClick={() => (n.external ? undefined : onRecenter(n))}
                role={n.external ? undefined : 'button'}
                aria-label={n.title}
              >
                <rect width={NODE_W} height={NODE_H} rx="4" />
                <text x="10" y="19" className="raw-graph-node-title">
                  {n.title.length > 24 ? `${n.title.slice(0, 23)}…` : n.title}
                </text>
                <text x="10" y="36" className="raw-graph-node-sub">
                  {n.external
                    ? t('rawExternal')
                    : [
                        t(`rawKind.${n.kind}`, { defaultValue: n.kind }),
                        n.visibility ? t(`rawVisibility.${n.visibility}`) : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                </text>
              </g>
            )
          })}
        </svg>
      </div>

      {layout.edges.length > 0 ? (
        <table className="table raw-graph-table">
          <thead>
            <tr>
              <th>{t('rawGraphColField')}</th>
              <th>{t('rawGraphColFrom')}</th>
              <th>{t('rawGraphColTarget')}</th>
              <th>{t('rawGraphColCardinality')}</th>
              <th>{t('rawGraphColDirection')}</th>
            </tr>
          </thead>
          <tbody>
            {layout.edges.map((e) => (
              <tr key={`${e.from}${e.to}${e.field}${e.kind}`}>
                <td className="mono">{e.field || '—'}</td>
                <td>
                  <NodeLink node={layout.byId.get(e.from)} onSelect={onSelect} />
                </td>
                <td>
                  <NodeLink node={layout.byId.get(e.to)} onSelect={onSelect} />
                </td>
                <td className="mono">
                  {e.kind === 'inherits' ? t('rawRefKind.inherits') : e.cardinality}
                </td>
                <td>{t(`rawGraphCategory.${categoryOf(e, layout.byId)}`)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}

/** Objet du tableau des liens : cliquable (il est sélectionné et s'ouvre dans
 * l'aperçu), sauf un type externe, qu'on ne peut pas ouvrir. */
function NodeLink({
  node,
  onSelect,
}: {
  node: RawGraphNode | undefined
  onSelect: (node: RawGraphNode) => void
}) {
  if (!node) return null
  if (node.external) return <span>{node.title}</span>
  return (
    <button type="button" className="btn-link" onClick={() => onSelect(node)}>
      {node.title}
    </button>
  )
}
