import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { RawAppModule, RawApplication as RawApplicationData } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

// Vue « Application » de l'Explorateur raw (epic « Cartographie applicative dans
// raw », ticket 0fa620be). Gael : « Je voudrais dans la partie raw pouvoir dessiner
// une vue applicative de l'application importée. » Un bloc par module (fichier de
// projet), ses dépendances en flèches, le contrat OpenAPI de la source à part.
// Blocs FIXES (« pas de souci si les modules sont fixes ») : la disposition est
// calculée, la même à chaque ouverture. Dessin SVG maison, comme le mode Graphe.

export const BLOCK_W = 210
export const BLOCK_H = 74
const COL_GAP = 96
const ROW_GAP = 30
const PAD = 24
const ZOOMS = [0.5, 0.67, 0.8, 1, 1.25, 1.5, 2]

export interface PlacedBlock {
  id: string
  kind: 'module' | 'contract'
  x: number
  y: number
}

export interface ApplicationLayout {
  blocks: Map<string, PlacedBlock>
  width: number
  height: number
}

const byTitle = (a: { title: string }, b: { title: string }) =>
  a.title.localeCompare(b.title, undefined, { sensitivity: 'base' })

export const contractId = (path: string) => `contract:${path}`

/**
 * Disposition calculée et stable : colonnes selon les dépendances (un module dont
 * personne ne dépend à gauche, ses dépendances à droite : points d'entrée →
 * application → cœur), ordre alphabétique dans une colonne. Les projets de test,
 * qui dépendent de tout, et les contrats vont sur une rangée à part, en bas.
 */
export function applicationLayout(app: RawApplicationData): ApplicationLayout {
  const main = app.modules.filter((m) => m.nature !== 'test')
  const ids = new Set(main.map((m) => m.id))
  const column = new Map(main.map((m) => [m.id, 0]))
  // Plus long chemin depuis les modules dont personne ne dépend ; borné par le
  // nombre de modules (une boucle de dépendances ne fait pas tourner sans fin).
  for (let pass = 0; pass < main.length; pass++) {
    let moved = false
    for (const m of main) {
      for (const dep of m.dependencies) {
        if (!dep.id || !ids.has(dep.id) || dep.id === m.id) continue
        const wanted = Math.min((column.get(m.id) ?? 0) + 1, main.length - 1)
        if ((column.get(dep.id) ?? 0) < wanted) {
          column.set(dep.id, wanted)
          moved = true
        }
      }
    }
    if (!moved) break
  }
  const columns = new Map<number, RawAppModule[]>()
  for (const m of [...main].sort(byTitle)) {
    const c = column.get(m.id) ?? 0
    columns.set(c, [...(columns.get(c) ?? []), m])
  }
  const blocks = new Map<string, PlacedBlock>()
  let rows = 0
  let cols = 0
  for (const [c, members] of columns) {
    cols = Math.max(cols, c + 1)
    rows = Math.max(rows, members.length)
    members.forEach((m, row) =>
      blocks.set(m.id, {
        id: m.id,
        kind: 'module',
        x: PAD + c * (BLOCK_W + COL_GAP),
        y: PAD + row * (BLOCK_H + ROW_GAP),
      }),
    )
  }
  const bottom = [
    ...[...app.contracts]
      .sort((a, b) => a.title.localeCompare(b.title))
      .map((c) => ({ id: contractId(c.path), kind: 'contract' as const })),
    ...app.modules
      .filter((m) => m.nature === 'test')
      .sort(byTitle)
      .map((m) => ({ id: m.id, kind: 'module' as const })),
  ]
  const bottomY = PAD + rows * (BLOCK_H + ROW_GAP) + (rows > 0 ? ROW_GAP : 0)
  bottom.forEach((b, i) => blocks.set(b.id, { ...b, x: PAD + i * (BLOCK_W + COL_GAP), y: bottomY }))
  cols = Math.max(cols, bottom.length)
  const height = (bottom.length ? bottomY + BLOCK_H : PAD + rows * (BLOCK_H + ROW_GAP) - ROW_GAP) + PAD
  return { blocks, width: PAD * 2 + cols * (BLOCK_W + COL_GAP) - COL_GAP, height }
}

/** Tracé d'un lien entre deux blocs : d'un côté à l'autre, ancres calculées. */
function edgePath(from: PlacedBlock, to: PlacedBlock): string {
  if (to.x > from.x) {
    const [x1, y1, x2, y2] = [from.x + BLOCK_W, from.y + BLOCK_H / 2, to.x, to.y + BLOCK_H / 2]
    const mid = (x1 + x2) / 2
    return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`
  }
  if (to.x < from.x) {
    const [x1, y1, x2, y2] = [from.x, from.y + BLOCK_H / 2, to.x + BLOCK_W, to.y + BLOCK_H / 2]
    const mid = (x1 + x2) / 2
    return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`
  }
  const down = to.y > from.y
  const [x1, y1] = [from.x + BLOCK_W / 2, down ? from.y + BLOCK_H : from.y]
  const [x2, y2] = [to.x + BLOCK_W / 2, down ? to.y : to.y + BLOCK_H]
  return `M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`
}

function shorten(text: string, max = 28): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

export function RawApplication({
  spaceId,
  branchId,
  slug,
  selected,
  onSelect,
  onOpenClasses,
}: {
  spaceId: string
  branchId: string
  slug: string
  /** Fichier du module sélectionné (aperçu de droite). */
  selected: string
  onSelect: (module: RawAppModule) => void
  /** « n classes » : sélectionner le module dans le menu de gauche. */
  onOpenClasses: (module: RawAppModule) => void
}) {
  const { t } = useTranslation()
  const [zoom, setZoom] = useState(3)
  const query = useQuery({
    queryKey: ['rawApplication', spaceId, branchId, slug],
    queryFn: () => api.getRawApplication(spaceId, branchId, slug),
  })
  const data = query.data
  const layout = useMemo(() => (data ? applicationLayout(data) : null), [data])
  const canvas = useRef<HTMLDivElement>(null)
  // Ouverte depuis le menu de gauche, la vue montre le module choisi.
  useEffect(() => {
    canvas.current
      ?.querySelector('.raw-app-block.selected')
      ?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [selected, layout])
  if (query.error) return <ErrorList messages={errorMessages(t, query.error)} />
  if (!data || !layout) return <p className="muted">{t('loading')}</p>
  const byId = new Map(data.modules.map((m) => [m.id, m]))
  const nature = (m: RawAppModule) => t(`rawAppNature.${m.nature}`, { defaultValue: m.nature })
  return (
    <div className="raw-app">
      <p className="raw-graph-banner">{t('rawAppBanner')}</p>
      <div className="raw-graph-controls">
        <span className="muted">{t('rawAppFiltersNote')}</span>
        <span className="raw-app-zoom" role="group" aria-label={t('rawAppZoom')}>
          <button type="button" className="btn btn-sm" onClick={() => setZoom((z) => Math.min(z + 1, ZOOMS.length - 1))}
            aria-label={t('rawAppZoomIn')}>+</button>
          <button type="button" className="btn btn-sm" onClick={() => setZoom((z) => Math.max(z - 1, 0))}
            aria-label={t('rawAppZoomOut')}>−</button>
          <button type="button" className="btn btn-sm" onClick={() => setZoom(3)}>
            {t('rawAppFit')}
          </button>
        </span>
      </div>
      <div className="raw-graph-canvas raw-app-canvas" ref={canvas}>
        <svg
          role="img"
          aria-label={t('rawAppLabel')}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          width={layout.width * ZOOMS[zoom]}
          height={layout.height * ZOOMS[zoom]}
        >
          <defs>
            <marker id="raw-app-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" className="raw-graph-arrow" />
            </marker>
          </defs>
          {data.modules.flatMap((m) =>
            m.dependencies
              .filter((d) => d.id && layout.blocks.has(d.id) && d.id !== m.id)
              .map((d) => (
                <g key={`${m.id}->${d.id}`} className="raw-graph-edge raw-app-edge" data-from={m.title}
                  data-to={byId.get(d.id ?? '')?.title}>
                  <path d={edgePath(layout.blocks.get(m.id)!, layout.blocks.get(d.id!)!)} markerEnd="url(#raw-app-arrow)" />
                </g>
              )),
          )}
          {data.contracts.flatMap((c) =>
            data.modules
              .filter((m) => m.counts.http > 0)
              .map((m) => (
                <g key={`${c.path}->${m.id}`} className="raw-graph-edge raw-app-edge contract">
                  <path d={edgePath(layout.blocks.get(contractId(c.path))!, layout.blocks.get(m.id)!)} />
                </g>
              )),
          )}
          {data.contracts.map((c) => {
            const b = layout.blocks.get(contractId(c.path))!
            return (
              <g key={c.path} className="raw-app-block contract" transform={`translate(${b.x},${b.y})`}>
                <title>{c.path}</title>
                <rect width={BLOCK_W} height={BLOCK_H} rx={6} />
                <text x={10} y={20} className="raw-app-title">
                  <tspan className="raw-app-tag">OAS</tspan> {shorten(c.title, 24)}
                </text>
                <text x={10} y={40} className="raw-app-sub">{t('rawAppContract')}</text>
                <text x={10} y={60} className="raw-app-sub">
                  {c.linked === undefined
                    ? t('rawAppOperations', { count: c.operations })
                    : t('rawAppOperationsLinked', { linked: c.linked, count: c.operations })}
                </text>
              </g>
            )
          })}
          {data.modules.map((m) => {
            const b = layout.blocks.get(m.id)!
            const isSelected = m.file === selected
            const sql = m.nature === 'sql'
            const target = m.targets.length ? ` · ${m.targets.join(', ')}` : ''
            return (
              <g
                key={m.id}
                className={`raw-app-block${isSelected ? ' selected' : ''}`}
                transform={`translate(${b.x},${b.y})`}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                aria-label={t('rawAppModule', { name: m.title })}
                onClick={() => onSelect(m)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onSelect(m)
                  }
                }}
              >
                <title>{m.project}</title>
                <rect width={BLOCK_W} height={BLOCK_H} rx={6} />
                <text x={10} y={20} className="raw-app-title">{shorten(m.title)}</text>
                <text x={10} y={38} className="raw-app-sub">{shorten(`${nature(m)}${target}`, 34)}</text>
                <text
                  x={10}
                  y={58}
                  className="raw-app-link"
                  role="link"
                  tabIndex={0}
                  aria-label={t(sql ? 'rawAppObjectsOf' : 'rawAppClassesOf', { count: m.counts.classes, name: m.title })}
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpenClasses(m)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.stopPropagation()
                      onOpenClasses(m)
                    }
                  }}
                >
                  {t(sql ? 'rawAppObjects' : 'rawAppClasses', { count: m.counts.classes })}
                </text>
                <text x={92} y={58} className="raw-app-sub">
                  {sql
                    ? t('rawAppProcedures', { count: m.counts.functions })
                    : `${t('rawAppEntries', { count: m.counts.entry_points })} · ${t('rawAppMethods', { count: m.counts.methods })}`}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
      <p className="muted small">{t('rawAppHint')}</p>
    </div>
  )
}
