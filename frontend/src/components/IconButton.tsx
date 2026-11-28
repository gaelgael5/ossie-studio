import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router-dom'

// Boutons à icône de toute la solution (ticket 1cca1f06). Avant, des caractères
// (✎, 🗑, ✕, ↑, ↓) dont le rendu dépendait de la police : la corbeille sortait en
// émoji couleur sur Mac, à côté d'un crayon monochrome. Les icônes sont dessinées
// ici, en trait de la couleur du texte, toutes à la même taille.
// Dans une ligne de tableau ou un élément de liste, le style les efface
// légèrement et les rend nettes au survol de la ligne (Gael, 28/11).

export type IconName = 'edit' | 'delete' | 'up' | 'down' | 'remove' | 'close'

const PATHS: Record<IconName, ReactNode> = {
  edit: (
    <>
      <path d="M3 13.5V16h2.5L14 7.5 11.5 5 3 13.5z" />
      <path d="M12.5 4l2.5 2.5" />
    </>
  ),
  delete: (
    <>
      <path d="M3.5 5.5h12" />
      <path d="M7.5 5.5V3.5h4v2" />
      <path d="M5 5.5l.8 10.5h7.4l.8-10.5" />
      <path d="M8 8.5v5M11 8.5v5" />
    </>
  ),
  up: <path d="M9.5 15V4M5 8.5l4.5-4.5 4.5 4.5" />,
  down: <path d="M9.5 4v11M5 10.5l4.5 4.5 4.5-4.5" />,
  remove: <path d="M5.5 5.5l8 8M13.5 5.5l-8 8" />,
  close: <path d="M5 5l9 9M14 5l-9 9" />,
}

export function Icon({ name }: { name: IconName }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 19 19"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  )
}

type Props = {
  icon: IconName
  /** Libellé accessible, repris en infobulle. */
  label: string
  /** Variante discrète, sans cadre (titre d'une page, pastille). */
  plain?: boolean
  className?: string
}

function classes(icon: IconName, plain: boolean | undefined, extra: string | undefined) {
  return ['btn-icon', plain ? 'btn-icon-plain' : 'btn btn-sm', icon === 'delete' ? 'btn-icon-danger' : '', extra ?? '']
    .filter(Boolean)
    .join(' ')
}

export function IconButton({
  icon,
  label,
  plain,
  className,
  ...rest
}: Props & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'aria-label' | 'title'>) {
  return (
    <button type="button" {...rest} className={classes(icon, plain, className)} aria-label={label} title={label}>
      <Icon name={icon} />
    </button>
  )
}

/** Même bouton, pour une navigation (le crayon qui ouvre une fiche). */
export function IconLink({ icon, label, plain, className, to }: Props & { to: string }) {
  return (
    <Link to={to} className={classes(icon, plain, className)} aria-label={label} title={label}>
      <Icon name={icon} />
    </Link>
  )
}
