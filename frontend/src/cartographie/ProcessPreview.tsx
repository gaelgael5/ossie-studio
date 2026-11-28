import type { CartoProcessPreview } from '../apiClient'

// Aperçu du dessin d'un processus BPMN (ticket 7e061c3e) : les formes du fichier aux
// positions de sa section `bpmndi`, en symboles simplifiés (piscine, couloir, tâche
// arrondie, événement rond, passerelle en losange, donnée) et en trait sarcelle sur la
// trame, comme l'aperçu des modèles. Sur une vignette, sans libellé ; dans la fenêtre
// d'import et sur la page d'un processus, avec les libellés des tâches et couloirs.

const MARGIN = 8
// Largeur moyenne d'un caractère, en part de la taille de police.
const CHAR_WIDTH = 0.56

/** Le libellé tient dans `room` pixels, sinon il est coupé par « … ». */
function fitLabel(label: string, room: number, font: number): string {
  const max = Math.floor(room / (font * CHAR_WIDTH))
  if (label.length <= max) return label
  return max <= 1 ? '' : `${label.slice(0, max - 1).trimEnd()}…`
}

/** Le libellé d'une tâche sur autant de lignes que sa hauteur le permet, coupé par
 * « … » sur la dernière s'il est encore trop long. */
function wrapLabel(label: string, width: number, height: number, font: number): string[] {
  const max = Math.floor(width / (font * CHAR_WIDTH))
  const room = Math.max(1, Math.floor(height / (font * 1.2)))
  const lines: string[] = []
  let current = ''
  for (const word of label.split(/\s+/).filter(Boolean)) {
    const next = current ? `${current} ${word}` : word
    if (next.length <= max || !current) current = next
    else {
      lines.push(current)
      current = word
    }
  }
  if (current) lines.push(current)
  // Le reste, plus long qu'une ligne, finit coupé sur la dernière ligne permise.
  const kept = lines.length > room ? [...lines.slice(0, room - 1), lines.slice(room - 1).join(' ')] : lines
  return kept.map((l) => fitLabel(l, width, font))
}

interface Props {
  preview: CartoProcessPreview
  width?: number
  height?: number
  labels?: boolean
}

export function ProcessPreview({ preview, width = 300, height = 130, labels = false }: Props) {
  const { boxes, links } = preview
  if (boxes.length === 0) {
    return <svg className="carto-process-preview" viewBox={`0 0 ${width} ${height}`} aria-hidden="true" />
  }
  const xs = [...boxes.flatMap((b) => [b.x, b.x + b.l]), ...links.flatMap((l) => l.points.map((p) => p[0]))]
  const ys = [...boxes.flatMap((b) => [b.y, b.y + b.h]), ...links.flatMap((l) => l.points.map((p) => p[1]))]
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const spanX = Math.max(...xs) - minX || 1
  const spanY = Math.max(...ys) - minY || 1
  const scale = Math.min((width - 2 * MARGIN) / spanX, (height - 2 * MARGIN) / spanY, 1)
  const offsetX = (width - spanX * scale) / 2
  const offsetY = (height - spanY * scale) / 2
  const px = (x: number) => offsetX + (x - minX) * scale
  const py = (y: number) => offsetY + (y - minY) * scale
  const font = Math.max(6, Math.min(11, 11 * scale * 1.6))
  return (
    <svg className="carto-process-preview" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      {boxes.map((b, i) => {
        const x = px(b.x)
        const y = py(b.y)
        const w = b.l * scale
        const h = b.h * scale
        const key = `b${i}`
        if (b.kind === 'event') {
          return <circle key={key} className="carto-bpmn-shape" cx={x + w / 2} cy={y + h / 2} r={Math.min(w, h) / 2} />
        }
        if (b.kind === 'gateway') {
          const points = `${x + w / 2},${y} ${x + w},${y + h / 2} ${x + w / 2},${y + h} ${x},${y + h / 2}`
          return <polygon key={key} className="carto-bpmn-shape" points={points} />
        }
        const rounded = b.kind === 'task' ? Math.min(6, w / 6) : 0
        return (
          <g key={key}>
            <rect
              className={`carto-bpmn-shape carto-bpmn-${b.kind}`}
              x={x}
              y={y}
              width={w}
              height={h}
              rx={rounded}
            />
            {labels && b.label && (b.kind === 'task' || b.kind === 'data') ? (
              <TaskLabel label={b.label} x={x} y={y} w={w} h={h} font={font} />
            ) : null}
            {labels && b.label && (b.kind === 'pool' || b.kind === 'lane') ? (
              <text
                className="carto-bpmn-label"
                x={x + font}
                y={y + h / 2}
                fontSize={font}
                textAnchor="middle"
                transform={`rotate(-90 ${x + font} ${y + h / 2})`}
              >
                {fitLabel(b.label, h - 6, font)}
              </text>
            ) : null}
          </g>
        )
      })}
      {links.map((l, i) => (
        <polyline
          key={`l${i}`}
          className="carto-bpmn-flow"
          points={l.points.map((p) => `${px(p[0])},${py(p[1])}`).join(' ')}
        />
      ))}
    </svg>
  )
}

function TaskLabel({ label, x, y, w, h, font }: { label: string; x: number; y: number; w: number; h: number; font: number }) {
  const lines = wrapLabel(label, w - 6, h - 4, font)
  const lineHeight = font * 1.2
  const top = y + h / 2 - ((lines.length - 1) * lineHeight) / 2
  return (
    <text className="carto-bpmn-label" fontSize={font} textAnchor="middle" dominantBaseline="middle">
      {lines.map((line, i) => (
        <tspan key={i} x={x + w / 2} y={top + i * lineHeight}>
          {line}
        </tspan>
      ))}
    </text>
  )
}
