import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import type { RailEdge, RailMapData, RailNode } from '../apiClient'

// Palette d'Okabe-Ito : distinguable en cas de daltonisme. Combinée à une forme
// par voie, la lecture ne repose jamais sur la seule couleur.
const PALETTE = ['#0072B2', '#009E73', '#D55E00', '#CC79A7', '#E69F00', '#56B4E9', '#000000']
const LANE_GAP = 46
const ROW_GAP = 40
const MARGIN_X = 28
const MARGIN_TOP = 26
// Libellé d'une gare (version), écrit à droite de sa voie : décalage depuis le
// centre de la gare, et largeur moyenne d'un caractère (12 px, demi-gras).
const LABEL_OFFSET = 14
const LABEL_CHAR_WIDTH = 7.5

const color = (lane: number) => PALETTE[lane % PALETTE.length]
const stationLabel = (n: RailNode) => `${n.number} · ${n.label ?? ''}`

interface Props {
  data: RailMapData
  selected: number | null
  onSelectVersion: (n: number) => void
  onLoadOlder: () => void
  formatDate: (iso: string) => string
}

function shape(lane: number, kind: string, cx: number, cy: number, shapeName: string): ReactElement {
  const c = color(lane)
  const station = kind === 'version'
  const unpub = kind === 'unpublished'
  const r = station ? 9 : 5
  // Non publié : contour pointillé, sans remplissage (arrêt « fantôme »).
  // Sinon : pastille pleine cerclée de blanc pour se détacher des voisins.
  const common = {
    fill: unpub ? 'none' : c,
    stroke: unpub ? c : '#ffffff',
    strokeWidth: station ? 2.5 : unpub ? 2 : 1.5,
    strokeDasharray: unpub ? '3 2' : undefined,
  }
  switch (shapeName) {
    case 'square':
      return <rect x={cx - r} y={cy - r} width={r * 2} height={r * 2} rx={station ? 3 : 1} {...common} />
    case 'triangle':
      return <polygon points={`${cx},${cy - r} ${cx + r},${cy + r} ${cx - r},${cy + r}`} {...common} />
    case 'diamond':
      return <polygon points={`${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`} {...common} />
    default:
      return <circle cx={cx} cy={cy} r={r} {...common} />
  }
}

export function RailMap({ data, selected, onSelectVersion, onLoadOlder, formatDate }: Props) {
  const { t } = useTranslation()
  const [versionsOnly, setVersionsOnly] = useState(false)

  const shapeByLane = useMemo(() => {
    const map = new Map<number, string>()
    data.lines.forEach((l) => map.set(l.lane, l.shape))
    return map
  }, [data.lines])

  const x = (lane: number) => MARGIN_X + lane * LANE_GAP
  const y = (row: number) => MARGIN_TOP + row * ROW_GAP
  const lanes = data.lines.length
  const rows = data.nodes.length
  const lanesWidth = MARGIN_X * 2 + Math.max(0, lanes - 1) * LANE_GAP + 2
  // Le dessin doit aussi contenir les libellés des gares, sinon ils sont rognés
  // (la largeur des voies seule ne laisse que quelques pixels au texte).
  const labelsRight = data.nodes
    .filter((n) => n.kind === 'version')
    .reduce(
      (max, n) => Math.max(max, x(n.lane) + LABEL_OFFSET + stationLabel(n).length * LABEL_CHAR_WIDTH),
      0,
    )
  const width = Math.max(lanesWidth, Math.ceil(labelsRight) + MARGIN_X / 2)
  const height = MARGIN_TOP * 2 + Math.max(0, rows - 1) * ROW_GAP

  // Étendue de chaque voie, pour tracer la « ligne » continue.
  const extent = useMemo(() => {
    const m = new Map<number, { min: number; max: number }>()
    data.nodes.forEach((n) => {
      const e = m.get(n.lane)
      if (!e) m.set(n.lane, { min: n.row, max: n.row })
      else m.set(n.lane, { min: Math.min(e.min, n.row), max: Math.max(e.max, n.row) })
    })
    return m
  }, [data.nodes])

  const edgeVisible = (e: RailEdge) => !versionsOnly || e.kind !== 'normal'
  const nodeVisible = (n: RailNode) => !versionsOnly || n.kind === 'version' || n.is_switch

  const edgePath = (e: RailEdge): string => {
    const x1 = x(e.from_lane)
    const y1 = y(e.from_row)
    const x2 = x(e.to_lane)
    const y2 = e.to_row === null ? height - 4 : y(e.to_row)
    if (e.to_row === null) return `M${x1},${y1} L${x1},${y2}`
    if (x1 === x2) return `M${x1},${y1} L${x2},${y2}`
    const mid = (y1 + y2) / 2
    return `M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`
  }

  const title = (n: RailNode): string => {
    const parts = [n.subject, `${n.author} · ${formatDate(n.date)}`, n.short]
    if (n.kind === 'version' && n.number !== null) {
      parts.unshift(t('railStation', { number: n.number, label: n.label ?? '' }))
    }
    return parts.join('\n')
  }

  const hasVersions = data.nodes.some((n) => n.kind === 'version')

  return (
    <div>
      <div
        style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}
      >
        <button
          type="button"
          className="btn btn-sm"
          aria-pressed={versionsOnly}
          onClick={() => setVersionsOnly((v) => !v)}
        >
          {versionsOnly ? t('showAll') : t('versionsOnly')}
        </button>
        <ul
          style={{ display: 'flex', gap: 14, listStyle: 'none', margin: 0, padding: 0, flexWrap: 'wrap' }}
        >
          {data.lines.map((l) => (
            <li
              key={l.ref}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}
            >
              <svg width="18" height="18" aria-hidden="true">
                {shape(l.lane, 'commit', 9, 9, shapeByLane.get(l.lane) ?? 'circle')}
              </svg>
              {l.kind === 'root' ? `${l.name} (${t('kindRoot')})` : l.name}
            </li>
          ))}
        </ul>
      </div>

      {!hasVersions ? <p>{t('noVersionsYet')}</p> : null}

      <div
        style={{
          overflow: 'auto',
          maxHeight: 460,
          border: '1px solid var(--border-strong)',
          borderRadius: 'var(--radius)',
          background: 'var(--panel)',
        }}
      >
        {/* overflow visible : un libellé plus large que l'estimation reste lisible. */}
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={t('railmapAria')}
          style={{ display: 'block', overflow: 'visible' }}
        >
          {/* Les « lignes » : une voie continue par branche. */}
          {data.lines.map((l) => {
            const e = extent.get(l.lane)
            if (!e) return null
            return (
              <line
                key={`lane-${l.lane}`}
                x1={x(l.lane)}
                y1={y(e.min)}
                x2={x(l.lane)}
                y2={y(e.max)}
                stroke={color(l.lane)}
                strokeWidth={3}
                strokeOpacity={0.35}
              />
            )
          })}
          {/* Arêtes : filiation, aiguillages (switch) et jonctions (merge). */}
          {data.edges.filter(edgeVisible).map((e) => (
            <path
              key={`${e.child}-${e.parent}`}
              d={edgePath(e)}
              fill="none"
              stroke={color(e.kind === 'merge' ? e.to_lane : e.from_lane)}
              strokeWidth={e.kind === 'normal' ? 3 : 2.5}
              strokeDasharray={e.kind === 'merge' ? '5 3' : undefined}
              strokeOpacity={0.8}
            />
          ))}
          {/* Nœuds : arrêts, gares (versions), non publiés. */}
          {data.nodes.filter(nodeVisible).map((n) => {
            const cx = x(n.lane)
            const cy = y(n.row)
            const isStation = n.kind === 'version'
            const activate =
              isStation && n.number !== null ? () => onSelectVersion(n.number as number) : undefined
            return (
              <g
                key={n.sha}
                style={{ cursor: activate ? 'pointer' : 'default' }}
                tabIndex={activate ? 0 : undefined}
                role={activate ? 'button' : undefined}
                aria-label={activate ? title(n) : undefined}
                onClick={activate}
                onKeyDown={
                  activate
                    ? (ev) => {
                        if (ev.key === 'Enter' || ev.key === ' ') {
                          ev.preventDefault()
                          activate()
                        }
                      }
                    : undefined
                }
              >
                <title>{title(n)}</title>
                {isStation && selected === n.number ? (
                  <circle cx={cx} cy={cy} r={14} fill="none" stroke={color(n.lane)} strokeWidth={2} />
                ) : null}
                {shape(n.lane, n.kind, cx, cy, shapeByLane.get(n.lane) ?? 'circle')}
                {isStation ? (
                  <text
                    x={cx + LABEL_OFFSET}
                    y={cy + 4}
                    style={{ fontSize: 12, fontWeight: 600 }}
                    fill="currentColor"
                  >
                    {stationLabel(n)}
                  </text>
                ) : null}
              </g>
            )
          })}
        </svg>
      </div>

      {data.has_more ? (
        <button type="button" className="btn btn-sm" onClick={onLoadOlder} style={{ marginTop: 8 }}>
          {t('loadOlder')}
        </button>
      ) : null}
    </div>
  )
}
