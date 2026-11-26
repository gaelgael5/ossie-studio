import type { CartoModelPreview } from '../apiClient'

// Aperçu de la première vue d'un modèle sur sa vignette (ticket 5a05c7bb) : les cadres
// et les traits du dessin, ramenés à la taille de la vignette, en trait sarcelle sur
// la trame (pas de couleurs de couche ArchiMate dans l'interface). Les libellés
// suivent le cadre ; un aperçu vide reste une trame.

const WIDTH = 300
const HEIGHT = 130
const MARGIN = 8
/** En haut, la place du nombre de vues posé sur l'aperçu. */
const MARGIN_TOP = 28
/** Largeur moyenne d'un caractère du libellé (7 px de corps). */
const CHAR = 4

/** Le libellé coupé à la largeur du cadre. */
function fit(label: string, width: number): string {
  const room = Math.floor((width - 6) / CHAR)
  return label.length <= room ? label : `${label.slice(0, Math.max(room - 1, 1))}…`
}

export function ModelPreview({ preview }: { preview: CartoModelPreview }) {
  const { boxes, links } = preview
  if (boxes.length === 0) {
    return <svg className="carto-model-preview" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" />
  }
  const minX = Math.min(...boxes.map((b) => b.x))
  const minY = Math.min(...boxes.map((b) => b.y))
  const maxX = Math.max(...boxes.map((b) => b.x + b.l))
  const maxY = Math.max(...boxes.map((b) => b.y + b.h))
  // Jamais agrandi au-delà de la taille réelle : un petit modèle reste petit.
  const scale = Math.min((WIDTH - 2 * MARGIN) / (maxX - minX || 1), (HEIGHT - MARGIN - MARGIN_TOP) / (maxY - minY || 1), 1)
  const offsetX = (WIDTH - (maxX - minX) * scale) / 2
  const offsetY = MARGIN_TOP + (HEIGHT - MARGIN - MARGIN_TOP - (maxY - minY) * scale) / 2
  const px = (x: number) => offsetX + (x - minX) * scale
  const py = (y: number) => offsetY + (y - minY) * scale
  return (
    <svg className="carto-model-preview" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true">
      {links.map((link, i) => (
        <line key={`l${i}`} className="carto-preview-link" x1={px(link.x1)} y1={py(link.y1)} x2={px(link.x2)} y2={py(link.y2)} />
      ))}
      {boxes.map((box, i) => (
        <g key={`b${i}`}>
          <rect
            className={`carto-preview-box carto-preview-${box.kind}`}
            x={px(box.x)}
            y={py(box.y)}
            width={box.l * scale}
            height={box.h * scale}
          />
          {box.l * scale > 30 ? (
            <text className="carto-preview-label" x={px(box.x) + 4} y={py(box.y) + 10}>
              {fit(box.label, box.l * scale)}
            </text>
          ) : null}
        </g>
      ))}
    </svg>
  )
}
