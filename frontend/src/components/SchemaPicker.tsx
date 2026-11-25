import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'

// Liste déroulante à cocher des schémas d'une source base (eebe6994), lue
// depuis la base avec le secret de la connexion. Aucune case cochée = tous les
// schémas : c'est le sens du manifeste sans clé `schemas`, qu'on ne change pas.
// Si la lecture échoue (droits, base injoignable), on retombe sur la saisie
// libre : la déclaration de la source ne doit jamais en dépendre.
export function SchemaPicker({
  connectionId,
  database,
  value,
  onChange,
}: {
  connectionId: string
  database: string
  value: string[]
  onChange: (next: string[]) => void
}) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState('')
  // La base se tape lettre à lettre : on attend une pause avant d'interroger
  // la base, sinon chaque frappe ouvrirait une connexion.
  const [db, setDb] = useState(database)
  useEffect(() => {
    const id = setTimeout(() => setDb(database), 600)
    return () => clearTimeout(id)
  }, [database])

  const schemas = useQuery({
    queryKey: ['db-schemas', connectionId, db],
    queryFn: () => api.listDbSchemas(connectionId, db),
    enabled: !!connectionId && !!db.trim(),
    retry: false,
    staleTime: 60_000,
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
  if (!connectionId || !db.trim()) return <span className="muted small">{t('schemaPickerNeedsBase')}</span>
  if (schemas.isPending) return <span className="muted small">{t('schemaPickerLoading')}</span>
  if (schemas.isError) {
    return (
      <div className="stack">
        <span className="field-hint text-warn">
          {t('schemaPickerFailed', { cause: errorMessages(t, schemas.error).join(' ') })}
        </span>
        {freeText}
      </div>
    )
  }

  const found = schemas.data.schemas
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
