import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, sessionCapabilities } from '../apiClient'
import type { RawAppModule, RawApplication as RawApplicationData, RawLinkAnchors } from '../apiClient'
import { errorMessages } from '../errors'
import { useSession } from '../hooks'
import { ErrorList } from '../components/ErrorList'
import { IconButton } from '../components/IconButton'

// Vue « Application » de l'Explorateur raw (epic « Cartographie applicative dans
// raw », ticket 0fa620be). Gael : « Je voudrais dans la partie raw pouvoir dessiner
// une vue applicative de l'application importée. » Un bloc par module (fichier de
// projet), ses dépendances en flèches, le contrat OpenAPI de la source à part.
// Blocs FIXES (« pas de souci si les modules sont fixes ») : la disposition est
// calculée, la même à chaque ouverture. Dessin SVG maison, comme le mode Graphe.
// Ancres des liens déplaçables (ticket a1fd0663) : clic sur un lien, puis glisser
// une extrémité (ou cliquer une ancre) ; le choix va dans le `.view` du module de
// départ, dans le brouillon de la personne.

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

export type Side = 'top' | 'right' | 'bottom' | 'left'
export interface FixedAnchor {
  side: Side
  at: number
}
interface Point {
  x: number
  y: number
}

/** Ancres proposées sur un bloc : le milieu de chaque côté, et deux points de plus
 * sur les côtés longs (haut et bas). */
export const ANCHOR_SLOTS: FixedAnchor[] = [
  { side: 'top', at: 0.25 },
  { side: 'top', at: 0.5 },
  { side: 'top', at: 0.75 },
  { side: 'right', at: 0.5 },
  { side: 'bottom', at: 0.25 },
  { side: 'bottom', at: 0.5 },
  { side: 'bottom', at: 0.75 },
  { side: 'left', at: 0.5 },
]
const SNAP = 18 // distance de capture d'une ancre au lâcher d'une poignée
const MOVE = 4 // en deçà, une poignée n'a pas bougé (simple clic)

export function anchorPoint(b: PlacedBlock, a: FixedAnchor): Point {
  if (a.side === 'top') return { x: b.x + BLOCK_W * a.at, y: b.y }
  if (a.side === 'bottom') return { x: b.x + BLOCK_W * a.at, y: b.y + BLOCK_H }
  if (a.side === 'left') return { x: b.x, y: b.y + BLOCK_H * a.at }
  return { x: b.x + BLOCK_W, y: b.y + BLOCK_H * a.at }
}

/** Ancres calculées d'un lien : d'un côté à l'autre selon la place des blocs. */
export function autoAnchors(from: PlacedBlock, to: PlacedBlock): [FixedAnchor, FixedAnchor] {
  if (to.x > from.x) return [{ side: 'right', at: 0.5 }, { side: 'left', at: 0.5 }]
  if (to.x < from.x) return [{ side: 'left', at: 0.5 }, { side: 'right', at: 0.5 }]
  return to.y > from.y
    ? [{ side: 'bottom', at: 0.5 }, { side: 'top', at: 0.5 }]
    : [{ side: 'top', at: 0.5 }, { side: 'bottom', at: 0.5 }]
}

/** Les ancres d'un lien : celles du `.view`, l'ancre calculée pour `auto`. */
export function linkAnchors(from: PlacedBlock, to: PlacedBlock, stored?: RawLinkAnchors): [FixedAnchor, FixedAnchor] {
  const [start, end] = autoAnchors(from, to)
  return [
    stored && stored.from !== 'auto' ? stored.from : start,
    stored && stored.to !== 'auto' ? stored.to : end,
  ]
}

const NORMAL: Record<Side, [number, number]> = { top: [0, -1], right: [1, 0], bottom: [0, 1], left: [-1, 0] }

/** Tracé d'un lien : une courbe qui sort perpendiculairement du côté de chaque ancre. */
export function linkPath(p1: Point, s1: Side, p2: Point, s2: Side): string {
  const reach = (side: Side) =>
    Math.max(24, (NORMAL[side][0] ? Math.abs(p2.x - p1.x) : Math.abs(p2.y - p1.y)) / 2)
  const [n1, n2] = [NORMAL[s1], NORMAL[s2]]
  const c1 = { x: p1.x + n1[0] * reach(s1), y: p1.y + n1[1] * reach(s1) }
  const c2 = { x: p2.x + n2[0] * reach(s2), y: p2.y + n2[1] * reach(s2) }
  return `M${p1.x},${p1.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${p2.x},${p2.y}`
}

/** Tracé d'un lien aux ancres calculées (contrats). */
function edgePath(from: PlacedBlock, to: PlacedBlock): string {
  const [a, b] = autoAnchors(from, to)
  return linkPath(anchorPoint(from, a), a.side, anchorPoint(to, b), b.side)
}

const sameAnchor = (a: FixedAnchor, b: FixedAnchor) => a.side === b.side && a.at === b.at
const linkKey = (from: string, to: string) => `${from}->${to}`

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
  // Ancres des liens (ticket a1fd0663) : modifiables par qui peut collecter.
  const session = useSession()
  const canEdit = sessionCapabilities(session.data).includes('sources:collect')
  const queryClient = useQueryClient()
  const svgRef = useRef<SVGSVGElement>(null)
  const dragged = useRef(false)
  const [link, setLink] = useState<{ from: string; to: string } | null>(null)
  const [drag, setDrag] = useState<{ end: 'from' | 'to'; start: Point; at: Point } | null>(null)
  // Rendu immédiat : le choix vaut jusqu'à ce que la vue rechargée le porte.
  const [pending, setPending] = useState<{ base?: RawApplicationData; links: Record<string, RawLinkAnchors> }>({
    links: {},
  })
  const save = useMutation({
    mutationFn: (v: { module: RawAppModule; target: string; anchors: RawLinkAnchors }) =>
      api.setRawModuleLink(spaceId, branchId, slug, { module: v.module.file, target: v.target, ...v.anchors }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rawApplication', spaceId, branchId, slug] }),
    onError: () => setPending({ links: {} }),
  })
  if (query.error) return <ErrorList messages={errorMessages(t, query.error)} />
  if (!data || !layout) return <p className="muted">{t('loading')}</p>
  const byId = new Map(data.modules.map((m) => [m.id, m]))
  const pendingLinks = pending.base === data ? pending.links : {}
  const stored = (from: RawAppModule, to: string): RawLinkAnchors | undefined =>
    pendingLinks[linkKey(from.id, to)] ?? from.anchors?.[to]
  const current = link ? byId.get(link.from) : undefined
  const currentTarget = link ? byId.get(link.to) : undefined
  const fromBlock = link ? layout.blocks.get(link.from) : undefined
  const toBlock = link ? layout.blocks.get(link.to) : undefined
  const ends =
    current && currentTarget && fromBlock && toBlock
      ? linkAnchors(fromBlock, toBlock, stored(current, currentTarget.id))
      : null
  const chosen = current && link ? stored(current, link.to) : undefined
  const isAuto = !chosen || (chosen.from === 'auto' && chosen.to === 'auto')

  const write = (anchors: RawLinkAnchors) => {
    if (!current || !link) return
    setPending((p) => ({
      base: data,
      links: { ...(p.base === data ? p.links : {}), [linkKey(current.id, link.to)]: anchors },
    }))
    save.mutate({ module: current, target: link.to, anchors })
  }
  const choose = (end: 'from' | 'to', anchor: FixedAnchor) => {
    if (!current || !link) return
    const now = stored(current, link.to) ?? { from: 'auto', to: 'auto' }
    write({ ...now, [end]: anchor })
  }
  const toSvg = (e: ReactPointerEvent): Point | null => {
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM?.()
    if (!svg || !ctm || !svg.createSVGPoint) return null
    const p = svg.createSVGPoint()
    p.x = e.clientX
    p.y = e.clientY
    const q = p.matrixTransform(ctm.inverse())
    return { x: q.x, y: q.y }
  }
  const startDrag = (end: 'from' | 'to', e: ReactPointerEvent) => {
    const at = toSvg(e)
    if (!at) return
    e.stopPropagation()
    svgRef.current?.setPointerCapture?.(e.pointerId)
    setDrag({ end, start: at, at })
  }
  const moveDrag = (e: ReactPointerEvent) => {
    if (!drag) return
    const at = toSvg(e)
    if (at) setDrag({ ...drag, at })
  }
  const endDrag = () => {
    if (!drag) return
    const block = drag.end === 'from' ? fromBlock : toBlock
    const moved = Math.hypot(drag.at.x - drag.start.x, drag.at.y - drag.start.y) > MOVE
    setDrag(null)
    if (!moved || !block) return
    dragged.current = true
    // Lâchée près d'une ancre du même bloc : elle s'y accroche ; ailleurs, retour.
    const near = ANCHOR_SLOTS.map((slot) => ({ slot, p: anchorPoint(block, slot) }))
      .map((c) => ({ ...c, d: Math.hypot(c.p.x - drag.at.x, c.p.y - drag.at.y) }))
      .sort((a, b) => a.d - b.d)[0]
    if (near && near.d <= SNAP) choose(drag.end, near.slot)
  }
  const pressKey = (action: () => void) => (e: ReactKeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      e.stopPropagation()
      action()
    }
  }
  const sideLabel = (side: Side) => t(`rawAppSide.${side}`)
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
      {link && current && currentTarget ? (
        <div className="raw-app-link-tools" role="group" aria-label={t('rawAppLinkLabel', { from: current.title, to: currentTarget.title })}>
          <strong className="mono small">{t('rawAppLinkLabel', { from: current.title, to: currentTarget.title })}</strong>
          {canEdit ? (
            <>
              <span className="muted small">{t('rawAppLinkHint')}</span>
              <button type="button" className="btn btn-sm" disabled={isAuto || save.isPending}
                onClick={() => write({ from: 'auto', to: 'auto' })}>
                {t('rawAppAnchorReset')}
              </button>
            </>
          ) : null}
          <IconButton icon="close" label={t('rawAppLinkClose')} onClick={() => setLink(null)} />
        </div>
      ) : null}
      <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
      <div className="raw-graph-canvas raw-app-canvas" ref={canvas}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setLink(null)
        }}>
        <svg
          ref={svgRef}
          role="img"
          aria-label={t('rawAppLabel')}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          width={layout.width * ZOOMS[zoom]}
          height={layout.height * ZOOMS[zoom]}
          onPointerMove={moveDrag}
          onPointerUp={endDrag}
          onPointerCancel={() => setDrag(null)}
          onClick={() => {
            // Un clic dans le vide désélectionne le lien (pas celui qui finit un glisser).
            if (dragged.current) dragged.current = false
            else setLink(null)
          }}
        >
          <defs>
            <marker id="raw-app-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" className="raw-graph-arrow" />
            </marker>
          </defs>
          {data.modules.flatMap((m) =>
            m.dependencies
              .filter((d) => d.id && layout.blocks.has(d.id) && d.id !== m.id)
              .map((d) => {
                const from = layout.blocks.get(m.id)!
                const to = layout.blocks.get(d.id!)!
                const isLink = link?.from === m.id && link?.to === d.id
                const [a, b] = linkAnchors(from, to, stored(m, d.id!))
                let [p1, p2] = [anchorPoint(from, a), anchorPoint(to, b)]
                if (isLink && drag) {
                  if (drag.end === 'from') p1 = drag.at
                  else p2 = drag.at
                }
                const path = linkPath(p1, a.side, p2, b.side)
                const select = () => setLink(isLink ? null : { from: m.id, to: d.id! })
                return (
                  <g key={linkKey(m.id, d.id!)} className={`raw-graph-edge raw-app-edge${isLink ? ' selected' : ''}`}
                    data-from={m.title} data-to={byId.get(d.id ?? '')?.title} role="button" tabIndex={0}
                    aria-pressed={isLink} aria-label={t('rawAppLinkLabel', { from: m.title, to: byId.get(d.id!)?.title })}
                    onClick={(e) => {
                      e.stopPropagation()
                      select()
                    }}
                    onKeyDown={pressKey(select)}>
                    <path className="raw-app-hit" d={path} />
                    <path d={path} markerEnd="url(#raw-app-arrow)" />
                  </g>
                )
              }),
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
          {link && ends && fromBlock && toBlock && current && currentTarget ? (
            <g className="raw-app-overlay">
              {canEdit
                ? ([['from', fromBlock, current, ends[0]], ['to', toBlock, currentTarget, ends[1]]] as const).flatMap(
                    ([end, block, owner, anchor]) =>
                      ANCHOR_SLOTS.map((slot) => {
                        const p = anchorPoint(block, slot)
                        const label = t('rawAppAnchor', { side: sideLabel(slot.side), at: Math.round(slot.at * 100), name: owner.title })
                        return (
                          <circle key={`${end}-${slot.side}-${slot.at}`} cx={p.x} cy={p.y} r={4.5}
                            className={`raw-app-slot${sameAnchor(slot, anchor) ? ' current' : ''}`} role="button" tabIndex={0}
                            aria-label={label} aria-pressed={sameAnchor(slot, anchor)}
                            onClick={(e) => {
                              e.stopPropagation()
                              choose(end, slot)
                            }}
                            onKeyDown={pressKey(() => choose(end, slot))}>
                            <title>{label}</title>
                          </circle>
                        )
                      }),
                  )
                : null}
              {canEdit
                ? ([['from', fromBlock, ends[0]], ['to', toBlock, ends[1]]] as const).map(([end, block, anchor]) => {
                    const p = drag?.end === end ? drag.at : anchorPoint(block, anchor)
                    return (
                      <circle key={end} cx={p.x} cy={p.y} r={6.5} className="raw-app-handle"
                        aria-label={t(end === 'from' ? 'rawAppLinkStart' : 'rawAppLinkEnd')}
                        onPointerDown={(e) => startDrag(end, e)} onClick={(e) => e.stopPropagation()}>
                        <title>{t(end === 'from' ? 'rawAppLinkStart' : 'rawAppLinkEnd')}</title>
                      </circle>
                    )
                  })
                : null}
            </g>
          ) : null}
        </svg>
      </div>
      <p className="muted small">{t('rawAppHint')}</p>
    </div>
  )
}
