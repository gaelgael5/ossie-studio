import { type KeyboardEvent, type PointerEvent, type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSession } from '../hooks'

// Barre de séparation redimensionnable entre une liste et son panneau de détail
// (ticket f74c8e74, epic « Property grid ». Gael, 28/11 : « prévois une splitbarre
// pour resizer les deux côtés »). Un seul composant pour les écrans liste / détail.
//
// Choix : composant maison plutôt qu'une dépendance. Le besoin tient en une grille
// à trois colonnes et quelques gestes (glisser, flèches, double-clic). La largeur
// est celle de la partie gauche, en pixels, retenue par écran et par utilisateur
// dans le stockage du navigateur (simple confort : sans stockage, la largeur par
// défaut s'applique).

/** Aucun des deux côtés ne descend sous cette largeur (critère 3). */
export const SPLIT_MIN = 260
/** Pas des flèches du clavier (critère 2). */
export const SPLIT_STEP = 24
const GUTTER = 12

function storageKey(id: string, userId: string | undefined): string {
  return `ossie.split.${id}.${userId ?? 'anonyme'}`
}

function readWidth(key: string): number | null {
  try {
    const value = Number(localStorage.getItem(key))
    return Number.isFinite(value) && value > 0 ? value : null
  } catch {
    return null
  }
}

function writeWidth(key: string, value: number | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, String(Math.round(value)))
  } catch {
    // Stockage indisponible (navigation privée…) : la largeur vaut pour la visite.
  }
}

/** Largeur ramenée dans les bornes du conteneur. */
export function clampWidth(width: number, container: number): number {
  const max = container - GUTTER - SPLIT_MIN
  if (max < SPLIT_MIN) return SPLIT_MIN
  return Math.min(Math.max(width, SPLIT_MIN), max)
}

type Props = {
  /** Identifiant de l'écran : la largeur retenue lui est propre (critère 4). */
  id: string
  /** Part de la largeur donnée à gauche par défaut (0,5 = moitié). */
  defaultRatio?: number
  left: ReactNode
  right: ReactNode
  className?: string
}

export function SplitPane({ id, defaultRatio = 0.5, left, right, className }: Props) {
  const { t } = useTranslation()
  const session = useSession()
  const key = storageKey(id, session.data?.user?.email)
  const host = useRef<HTMLDivElement>(null)
  const [container, setContainer] = useState(0)
  const [stored, setStored] = useState<number | null>(() => readWidth(key))
  const [dragging, setDragging] = useState(false)

  // L'utilisateur peut arriver après le premier rendu (session chargée) : relire.
  useEffect(() => setStored(readWidth(key)), [key])

  // Suivre la largeur du conteneur : une valeur retenue devenue trop grande pour la
  // fenêtre est ramenée dans les bornes, sans être réécrite.
  useLayoutEffect(() => {
    const node = host.current
    if (!node) return
    const measure = () => setContainer(node.getBoundingClientRect().width)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  // Trop étroit pour deux côtés de 260 px : le panneau passe sous la liste, la
  // barre disparaît (critère 6).
  const stacked = container > 0 && container < SPLIT_MIN * 2 + GUTTER
  const fallback = container > 0 ? container * defaultRatio : SPLIT_MIN
  const width = clampWidth(stored ?? fallback, container || SPLIT_MIN * 2 + GUTTER)

  const commit = useCallback(
    (next: number | null) => {
      setStored(next)
      writeWidth(key, next)
    },
    [key],
  )

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    // Bouton principal seulement (un clic droit ne déplace pas la barre).
    if (e.button > 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging || !host.current) return
    const box = host.current.getBoundingClientRect()
    setStored(clampWidth(e.clientX - box.left - GUTTER / 2, box.width))
  }
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    e.currentTarget.releasePointerCapture(e.pointerId)
    setDragging(false)
    writeWidth(key, width)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    commit(clampWidth(width + (e.key === 'ArrowRight' ? SPLIT_STEP : -SPLIT_STEP), container))
  }

  // Pendant le glissement, aucune sélection de texte parasite (critère 1).
  useEffect(() => {
    if (!dragging) return
    document.body.classList.add('split-dragging')
    return () => document.body.classList.remove('split-dragging')
  }, [dragging])

  const percent = container > 0 ? Math.round((width / container) * 100) : 50
  return (
    <div
      ref={host}
      className={`split-pane${stacked ? ' split-pane-stacked' : ''}${className ? ` ${className}` : ''}`}
      style={stacked ? undefined : { gridTemplateColumns: `${Math.round(width)}px ${GUTTER}px minmax(${SPLIT_MIN}px, 1fr)` }}
      data-testid={`split-${id}`}
    >
      <div className="split-pane-side">{left}</div>
      {stacked ? null : (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t('splitLabel')}
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          tabIndex={0}
          title={t('splitHint')}
          className={`split-pane-bar${dragging ? ' split-pane-bar-active' : ''}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
          onDoubleClick={() => commit(null)}
        >
          <span className="split-pane-grip" aria-hidden="true" />
        </div>
      )}
      <div className="split-pane-side">{right}</div>
    </div>
  )
}
