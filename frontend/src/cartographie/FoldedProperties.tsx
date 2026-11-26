import { useTranslation } from 'react-i18next'
import { SchemaFields } from '../referentiels/ValuePanel'
import type { FieldProblems } from '../referentiels/ValuePanel'

// Les propriétés d'un objet de cartographie tirées du schéma de son type (ticket
// 6f71bcba) : les obligatoires, les attributs ArchiMate et les renseignées sont
// affichés, les facultatives
// vides repliées dans une zone « n propriétés non renseignées », d'où « + Renseigner
// une propriété » en ouvre une. Repris par la fenêtre « Nouvelle relation » (b58a0a13).

export type Props = Record<string, unknown>

export function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0)
}

export function propertyNames(schema: Props | null | undefined): string[] {
  return Object.keys((schema?.properties ?? {}) as Props)
}

export function requiredOf(schema: Props | null | undefined): string[] {
  return Array.isArray(schema?.required) ? (schema.required as string[]) : []
}

/** Les propriétés obligatoires encore vides. */
export function missingRequired(schema: Props | null | undefined, values: Props): string[] {
  return requiredOf(schema).filter((p) => isEmpty(values[p]))
}

export function FoldedProperties({
  schema,
  values,
  saved = {},
  opened,
  readOnly,
  problems,
  onChange,
  onOpen,
}: {
  schema: Props
  values: Props
  /** Les valeurs enregistrées : une propriété renseignée reste affichée si on la vide. */
  saved?: Props
  opened: string[]
  readOnly: boolean
  problems: FieldProblems
  onChange: (next: Props) => void
  onOpen: (property: string) => void
}) {
  const { t } = useTranslation()
  const all = propertyNames(schema)
  const required = requiredOf(schema)
  // Les attributs propres au type ArchiMate (sens d'un accès…) ne se replient pas.
  const archimate = (p: string) => Boolean(((schema.properties ?? {}) as Record<string, Props>)[p]?.['x-archimate'])
  const visible = all.filter(
    (p) => required.includes(p) || archimate(p) || !isEmpty(saved[p]) || !isEmpty(values[p]) || opened.includes(p),
  )
  const folded = all.filter((p) => !visible.includes(p))
  return (
    <>
      {visible.length > 0 ? (
        <SchemaFields schema={schema} draft={values} readOnly={readOnly} problems={problems} onChange={onChange} names={visible} />
      ) : null}
      {folded.length > 0 ? (
        <div className="carto-folded">
          <span className="muted">{t('cartoFolded', { count: folded.length })}</span>
          {folded.map((p) => (
            <span key={p} className="pill">
              {p}
            </span>
          ))}
          {!readOnly ? (
            <select
              className="select carto-fill"
              aria-label={t('cartoFill')}
              value=""
              onChange={(e) => e.target.value && onOpen(e.target.value)}
            >
              <option value="">{t('cartoFill')}</option>
              {folded.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          ) : null}
        </div>
      ) : null}
    </>
  )
}
