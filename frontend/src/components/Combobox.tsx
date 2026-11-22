import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

export interface ComboOption {
  value: string
  label: string
}

const SHOWN = 100

// Liste déroulante filtrable : fermée, elle a l'aspect d'une liste native ; un clic
// la déplie avec un champ de filtre déjà actif (quelques caractères suffisent).
// Clavier : ↑/↓ pour parcourir, Entrée pour choisir, Échap pour refermer.
// La liste native ne sait pas filtrer, et une datalist ne restitue pas la
// valeur (elle rend le texte saisi) : d'où ce composant.
export function Combobox(props: {
  id: string
  options: ComboOption[]
  value: string
  onChange: (value: string) => void
  placeholder: string
  filterPlaceholder: string
  emptyText: string
  moreText: (count: number) => string
  status?: React.ReactNode
}) {
  const { id, options, value, onChange, placeholder, filterPlaceholder, emptyText, moreText, status } = props
  const listId = useId()
  const root = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [active, setActive] = useState(0)

  const needle = filter.trim().toLowerCase()
  const matching = options.filter((o) => o.label.toLowerCase().includes(needle))
  const shown = matching.slice(0, SHOWN)
  const chosen = options.find((o) => o.value === value)

  // Clic hors du composant : on referme (écoute du document, pas d'appel réseau).
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const toggle = () => {
    setFilter('')
    setActive(Math.max(0, options.findIndex((o) => o.value === value)))
    setOpen((o) => !o)
  }
  const pick = (option: ComboOption) => {
    onChange(option.value)
    setOpen(false)
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, shown.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const option = shown[active]
      if (option) pick(option)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="combo" ref={root}>
      <button
        id={id}
        type="button"
        className="select combo-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={toggle}
      >
        <span className={chosen ? '' : 'muted'}>{chosen ? chosen.label : placeholder}</span>
      </button>
      {open ? (
        <div className="combo-panel">
          <input
            className="input"
            autoFocus
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={shown[active] ? `${listId}-${active}` : undefined}
            placeholder={filterPlaceholder}
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value)
              setActive(0)
            }}
            onKeyDown={onKey}
          />
          {status}
          <ul className="combo-list" role="listbox" id={listId}>
            {shown.map((option, i) => (
              <li
                key={option.value}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={option.value === value}
                className={`combo-option${i === active ? ' active' : ''}${option.value === value ? ' chosen' : ''}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(option)}
              >
                {option.label}
              </li>
            ))}
            {status ? null : matching.length === 0 ? <li className="combo-note">{emptyText}</li> : null}
            {matching.length > SHOWN ? <li className="combo-note">{moreText(matching.length - SHOWN)}</li> : null}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
