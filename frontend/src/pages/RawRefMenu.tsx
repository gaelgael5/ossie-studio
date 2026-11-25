import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { RawExplorerMode, RawTarget } from '../apiClient'
import { errorMessages } from '../errors'

// Compteur « référencé par » d'une vignette (demande de Gael, 22/11/2026) : le
// nombre vient avec le contenu du dossier ; la liste ne se charge qu'à
// l'ouverture du menu (appel léger, lu dans l'index inverse de l'explorateur).

export function RefMenu({
  spaceId,
  branchId,
  slug,
  mode,
  file,
  count,
  onOpen,
}: {
  spaceId: string
  branchId: string
  slug: string
  mode: RawExplorerMode
  file: string
  count: number
  onOpen: (target: RawTarget) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLSpanElement>(null)
  const incoming = useQuery({
    queryKey: ['rawIncoming', spaceId, branchId, slug, mode, file],
    queryFn: () => api.getRawIncoming(spaceId, branchId, slug, mode, file),
    enabled: open,
    staleTime: 30_000,
    retry: false,
  })

  // Echap ou un clic ailleurs ferme le menu.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    const onClick = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open])

  if (count === 0) return null
  return (
    <span className="raw-ref-menu" ref={box}>
      <button
        type="button"
        className="raw-ref-count"
        aria-expanded={open}
        aria-label={t('rawReferencedByCount', { count })}
        title={t('rawReferencedByCount', { count })}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        onDoubleClick={(e) => e.stopPropagation()}
      >
        ← {count}
      </button>
      {open ? (
        <div className="raw-ref-popup" role="menu">
          <p className="panel-label">{t('rawReferencedByTitle', { count })}</p>
          {incoming.isLoading ? (
            <p className="small muted" aria-busy="true">
              <span className="spinner spinner-sm" aria-hidden="true" /> {t('rawLoading')}
            </p>
          ) : incoming.error ? (
            <p className="small text-danger">{errorMessages(t, incoming.error).join(' ')}</p>
          ) : (
            <ul className="list-plain">
              {(incoming.data?.incoming ?? []).map((r) => (
                <li key={`${r.target.slug}/${r.target.file}/${r.field}/${r.kind}`}>
                  <button
                    type="button"
                    role="menuitem"
                    className="raw-ref-item"
                    onClick={(e) => {
                      e.stopPropagation()
                      setOpen(false)
                      onOpen(r.target)
                    }}
                  >
                    <span className="raw-ref-title">{r.target.title}</span>
                    <span className="small muted">
                      {[
                        r.field,
                        t(`rawReferencedByKind.${r.kind}`, { defaultValue: r.kind }),
                        r.cardinality,
                        r.target.slug !== slug ? r.target.slug : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </span>
  )
}
