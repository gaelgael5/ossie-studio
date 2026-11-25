import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from './BusyButton'

// Liste déroulante à cocher des schémas d'une source base (eebe6994), lue
// depuis la base avec le secret de la connexion. Aucune case cochée = tous les
// schémas : c'est le sens du manifeste sans clé `schemas`, qu'on ne change pas.
// Si la lecture échoue (droits, base injoignable), on retombe sur la saisie
// libre : la déclaration de la source ne doit jamais en dépendre.
//
// Lecture À LA DEMANDE (89752930) : une lecture pendant la frappe ouvrait une
// session par pause de saisie (« DP », « D », « DB_ »…), donc une notification
// de double authentification à chaque fois. Seul le bouton « Rafraîchir » lit
// la base, plus une lecture unique à l'ouverture d'une source déjà déclarée.
export function SchemaPicker({
  connectionId,
  database,
  value,
  onChange,
  autoLoad = false,
}: {
  connectionId: string
  database: string
  value: string[]
  onChange: (next: string[]) => void
  autoLoad?: boolean
}) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState('')
  // Dernière demande : la base lue et un compteur, pour qu'un nouveau clic
  // relise vraiment la base même si elle n'a pas changé.
  const [request, setRequest] = useState<{ db: string; n: number } | null>(null)
  const ready = !!connectionId && !!database.trim()
  const refresh = () => setRequest((r) => ({ db: database.trim(), n: (r?.n ?? 0) + 1 }))

  const autoDone = useRef(false)
  useEffect(() => {
    if (autoLoad && ready && !autoDone.current) {
      autoDone.current = true
      setRequest({ db: database.trim(), n: 1 })
    }
  }, [autoLoad, ready, database])

  const schemas = useQuery({
    queryKey: ['db-schemas', connectionId, request?.db, request?.n],
    queryFn: () => api.listDbSchemas(connectionId, request!.db),
    enabled: !!connectionId && !!request,
    retry: false,
    staleTime: Infinity,
  })

  // Texte gardé tel que tapé : le reconstruire depuis la liste ferait
  // disparaître la virgule qu'on vient de taper.
  const [text, setText] = useState(value.join(', '))
  const freeText = (
    <input
      id="s-schemas"
      className="input"
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))
      }}
    />
  )
  const loading = !!request && schemas.isFetching
  const refreshButton = (
    <BusyButton
      type="button"
      className="btn btn-sm"
      busy={loading}
      busyText={t('schemaPickerLoading')}
      disabled={!ready}
      onClick={refresh}
    >
      ↻ {t('schemaPickerRefresh')}
    </BusyButton>
  )

  let body
  if (!request || (schemas.isPending && !schemas.isFetching)) {
    body = (
      <>
        <span className="muted small">{ready ? t('schemaPickerHint') : t('schemaPickerNeedsBase')}</span>
        {freeText}
      </>
    )
  } else if (schemas.isPending) {
    body = <span className="muted small">{t('schemaPickerLoading')}</span>
  } else if (schemas.isError) {
    body = (
      <>
        <span className="field-hint text-warn">
          {t('schemaPickerFailed', { cause: errorMessages(t, schemas.error).join(' ') })}
        </span>
        {freeText}
      </>
    )
  } else {
    body = <SchemaList found={schemas.data.schemas} value={value} onChange={onChange} filter={filter} setFilter={setFilter} />
  }

  return (
    <div className="stack">
      <div>{refreshButton}</div>
      {body}
    </div>
  )
}

function SchemaList({
  found,
  value,
  onChange,
  filter,
  setFilter,
}: {
  found: { name: string; tables: number }[]
  value: string[]
  onChange: (next: string[]) => void
  filter: string
  setFilter: (f: string) => void
}) {
  const { t } = useTranslation()
  const known = new Set(found.map((s) => s.name))
  // Un schéma déclaré que la base ne rend plus reste coché, signalé introuvable :
  // le décocher en silence changerait le manifeste sans que la personne le voie.
  const missing = value.filter((v) => !known.has(v))
  const rows = [...missing.map((name) => ({ name, tables: -1 })), ...found]
  const q = filter.trim().toLowerCase()
  const shown = q ? rows.filter((r) => r.name.toLowerCase().includes(q)) : rows
  const toggle = (name: string, on: boolean) =>
    onChange(on ? [...value, name] : value.filter((v) => v !== name))
  const summary =
    value.length === 0
      ? t('schemaPickerAll', { count: found.length })
      : value.length <= 3
        ? value.join(', ')
        : t('schemaPickerSome', { first: value.slice(0, 2).join(', '), count: value.length - 2 })

  return (
    <details className="raw-source-filter">
      <summary id="s-schemas" aria-label={t('fieldSchemas')}>
        {summary}
      </summary>
      <div className="raw-source-filter-menu">
        {rows.length > 8 ? (
          <input
            className="input"
            placeholder={t('schemaPickerFilter')}
            aria-label={t('schemaPickerFilter')}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        ) : null}
        <label>
          <input type="checkbox" checked={value.length === 0} onChange={() => onChange([])} />{' '}
          {t('schemaPickerAllOption')}
        </label>
        {found.length === 0 ? <span className="muted small">{t('schemaPickerEmpty')}</span> : null}
        {shown.map((r) => (
          <label key={r.name}>
            <input
              type="checkbox"
              checked={value.includes(r.name)}
              onChange={(e) => toggle(r.name, e.target.checked)}
            />{' '}
            <span className="mono">{r.name}</span>{' '}
            {r.tables < 0 ? (
              <span className="badge badge-warn">{t('schemaPickerMissing')}</span>
            ) : (
              <span className="muted small">{t('schemaPickerTables', { count: r.tables })}</span>
            )}
          </label>
        ))}
      </div>
    </details>
  )
}
