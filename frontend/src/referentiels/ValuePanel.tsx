import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { RefModel } from './modelRules'
import {
  type Kind,
  type PathPart,
  type Schema,
  type Value,
  emptyItem,
  fieldPath,
  getAt,
  isSimple,
  itemsOf,
  keyLabel,
  kindOf,
  move,
  parseInput,
  propertiesOf,
  requiredOf,
  schemaAt,
  setAt,
  shown,
} from './valueRules'

/** Problèmes de la valeur ouverte : chemin du champ → codes (« » : la valeur entière). */
export type FieldProblems = Map<string, string[]>

interface Ctx {
  draft: Value
  readOnly: boolean
  lockedKey: string[]
  problems: FieldProblems
  set: (path: PathPart[], next: unknown) => void
  open: (path: PathPart[]) => void
  /** Afficher sous chaque champ la description de son schéma (fiche d'un objet de cartographie). */
  describe?: boolean
}

/** La description d'un champ à afficher sous lui : `description`, sinon son titre
 * s'il dit autre chose que son nom. */
function hintOf(schema: Schema | undefined, name: string): string {
  const description = typeof schema?.description === 'string' ? schema.description : ''
  const title = typeof schema?.title === 'string' ? schema.title : ''
  return description || (title && title !== name ? title : '')
}

function Hint({ ctx, schema, name }: { ctx: Ctx; schema: Schema | undefined; name: string }) {
  const hint = ctx.describe ? hintOf(schema, name) : ''
  return hint ? <span className="field-hint">{hint}</span> : null
}

/** Fil d'Ariane d'un chemin ouvert dans le formulaire : « racine › paliers[2] › … ». */
function crumbsOf(rootLabel: string, path: PathPart[]): { label: string; path: PathPart[] }[] {
  const crumbs: { label: string; path: PathPart[] }[] = [{ label: rootLabel, path: [] }]
  path.forEach((part, i) => {
    if (typeof part === 'number') crumbs[crumbs.length - 1].label += `[${part}]`
    else crumbs.push({ label: part, path: path.slice(0, i + 1) })
    crumbs[crumbs.length - 1].path = path.slice(0, i + 1)
  })
  return crumbs
}

function Crumbs({ crumbs, onOpen, label }: { crumbs: { label: string; path: PathPart[] }[]; onOpen: (path: PathPart[]) => void; label: string }) {
  return (
    <nav className="breadcrumb ref-panel-crumbs" aria-label={label}>
      {crumbs.map((c, i) => (
        <span key={i}>
          {i > 0 ? ' › ' : ''}
          {i < crumbs.length - 1 ? (
            <button type="button" className="link-button" onClick={() => onOpen(c.path)}>
              {c.label}
            </button>
          ) : (
            c.label
          )}
        </span>
      ))}
    </nav>
  )
}

function useErrorText() {
  const { t } = useTranslation()
  return (codes: string[] | undefined) =>
    (codes ?? []).map((code) => t(`refValueError_${code}`, { defaultValue: t('refValueError_other') }))
}

function FieldInput({
  id,
  kind,
  schema,
  value,
  readOnly,
  label,
  onChange,
}: {
  id: string
  kind: Kind
  schema: Schema
  value: unknown
  readOnly: boolean
  label: string
  onChange: (next: unknown) => void
}) {
  const { t } = useTranslation()
  const text = shown(value)
  if (kind === 'enum') {
    // Libellés d'affichage facultatifs (attributs ArchiMate de la cartographie) :
    // `x-labels` pour chaque valeur, `x-unset` pour la valeur absente. La valeur
    // stockée reste celle de l'énumération.
    const labels = (schema['x-labels'] ?? {}) as Record<string, string>
    const unset = typeof schema['x-unset'] === 'string' ? schema['x-unset'] : t('refPanelEmptyOption')
    return (
      <select id={id} aria-label={label} className="input" value={text} disabled={readOnly} onChange={(e) => onChange(parseInput(kind, e.target.value))}>
        <option value="">{unset}</option>
        {(schema.enum as unknown[]).map((option) => (
          <option key={String(option)} value={String(option)}>
            {labels[String(option)] ?? String(option)}
          </option>
        ))}
      </select>
    )
  }
  if (kind === 'boolean') {
    return (
      <select id={id} aria-label={label} className="input" value={text} disabled={readOnly} onChange={(e) => onChange(parseInput(kind, e.target.value))}>
        <option value="">{t('refPanelEmptyOption')}</option>
        <option value="true">{t('refPanelYes')}</option>
        <option value="false">{t('refPanelNo')}</option>
      </select>
    )
  }
  const type = kind === 'integer' || kind === 'number' ? 'number' : kind === 'date' ? 'date' : 'text'
  return (
    <input
      id={id}
      aria-label={label}
      className="input"
      type={type}
      step={kind === 'number' ? 'any' : undefined}
      placeholder={kind === 'datetime' ? '2026-11-24T10:00:00Z' : undefined}
      value={text}
      readOnly={readOnly}
      onChange={(e) => onChange(parseInput(kind, e.target.value))}
    />
  )
}

function Errors({ codes }: { codes: string[] | undefined }) {
  const text = useErrorText()
  const messages = text(codes)
  if (messages.length === 0) return null
  return <span className="field-error text-danger small">{messages.join(' · ')}</span>
}

function SimpleList({ ctx, path, items, label }: { ctx: Ctx; path: PathPart[]; items: Schema | undefined; label: string }) {
  const { t } = useTranslation()
  const list = (getAt(ctx.draft, path) as unknown[] | undefined) ?? []
  const kind = kindOf(items)
  return (
    <div className="stack ref-simple-list">
      {list.map((item, index) => (
        <div key={index} className="row">
          <FieldInput
            id={`f-${fieldPath([...path, index])}`}
            kind={kind}
            schema={items ?? {}}
            value={item}
            readOnly={ctx.readOnly}
            label={`${label}[${index}]`}
            onChange={(next) => ctx.set([...path, index], next)}
          />
          <Errors codes={ctx.problems.get(fieldPath([...path, index]))} />
          {!ctx.readOnly ? (
            <button type="button" className="btn btn-sm" aria-label={t('refPanelItemRemove', { index })} onClick={() => ctx.set(path, list.filter((_, i) => i !== index))}>
              {'✕'}
            </button>
          ) : null}
        </div>
      ))}
      {!ctx.readOnly ? (
        <button type="button" className="btn btn-sm" onClick={() => ctx.set(path, [...list, emptyItem(items) ?? ''])}>
          {t('refPanelAddItem')}
        </button>
      ) : null}
    </div>
  )
}

/** Un tableau d'objets en sous-tableau : une colonne par propriété simple de
 * l'élément ; un objet ou un tableau d'un élément s'ouvre dans le panneau. */
function ArrayTable({ ctx, path, items, label }: { ctx: Ctx; path: PathPart[]; items: Schema | undefined; label: string }) {
  const { t } = useTranslation()
  const list = (getAt(ctx.draft, path) as Value[] | undefined) ?? []
  const columns = propertiesOf(items).filter(([, s]) => isSimple(s))
  const nested = propertiesOf(items).filter(([, s]) => !isSimple(s))
  return (
    <div className="stack">
      <div className="table-wrap">
        <table className="table ref-subtable" aria-label={label}>
          <thead>
            <tr>
              <th>#</th>
              {columns.map(([name]) => (
                <th key={name}>
                  {name}
                  {requiredOf(items).includes(name) ? ' *' : ''}
                </th>
              ))}
              {nested.length > 0 ? <th /> : null}
              {!ctx.readOnly ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {list.map((item, index) => (
              <tr key={index}>
                <td className="muted">{index}</td>
                {columns.map(([name, schema]) => {
                  const cell = [...path, index, name]
                  const codes = ctx.problems.get(fieldPath(cell))
                  return (
                    <td key={name} className={codes ? 'cell-error' : undefined}>
                      <FieldInput
                        id={`f-${fieldPath(cell)}`}
                        kind={kindOf(schema)}
                        schema={schema}
                        value={item?.[name]}
                        readOnly={ctx.readOnly}
                        label={`${label}[${index}] ${name}`}
                        onChange={(next) => ctx.set(cell, next)}
                      />
                      <Errors codes={codes} />
                    </td>
                  )
                })}
                {nested.length > 0 ? (
                  <td>
                    <button type="button" className="btn btn-sm" onClick={() => ctx.open([...path, index])}>
                      {t('refPanelOpen')}
                    </button>
                  </td>
                ) : null}
                {!ctx.readOnly ? (
                  <td className="row">
                    <button type="button" className="btn btn-sm" aria-label={t('refPanelItemUp', { index })} disabled={index === 0} onClick={() => ctx.set(path, move(list, index, index - 1))}>
                      {'↑'}
                    </button>
                    <button type="button" className="btn btn-sm" aria-label={t('refPanelItemDown', { index })} disabled={index === list.length - 1} onClick={() => ctx.set(path, move(list, index, index + 1))}>
                      {'↓'}
                    </button>
                    <button type="button" className="btn btn-sm" aria-label={t('refPanelItemRemove', { index })} onClick={() => ctx.set(path, list.filter((_, i) => i !== index))}>
                      {'✕'}
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!ctx.readOnly ? (
        <button type="button" className="btn btn-sm" onClick={() => ctx.set(path, [...list, emptyItem(items)])}>
          {t('refPanelAddItem')}
        </button>
      ) : null}
    </div>
  )
}

/** Les champs d'un objet du modèle (la valeur elle-même, un objet imbriqué ou un
 * élément de tableau ouvert dans le panneau). */
function ObjectFields({
  ctx,
  schema,
  path,
  names,
}: {
  ctx: Ctx
  schema: Schema | undefined
  path: PathPart[]
  /** Seulement ces propriétés de premier niveau (fiche d'un objet : les vides repliées). */
  names?: string[]
}) {
  const { t } = useTranslation()
  const required = requiredOf(schema)
  return (
    <div className="stack">
      {propertiesOf(schema).filter(([name]) => !names || names.includes(name)).map(([name, sub]) => {
        const here = [...path, name]
        const field = fieldPath(here)
        const kind = kindOf(sub)
        const label = `${name}${required.includes(name) ? ' *' : ''}`
        if (kind === 'object') {
          return (
            <fieldset key={name} className="ref-fieldset">
              <legend>{name}</legend>
              <Hint ctx={ctx} schema={sub} name={name} />
              <ObjectFields ctx={ctx} schema={sub} path={here} />
            </fieldset>
          )
        }
        if (kind === 'array') {
          const items = itemsOf(sub)
          return (
            <div key={name} className="field">
              <span className="field-label">{label}</span>
              {kindOf(items) === 'object' ? (
                <ArrayTable ctx={ctx} path={here} items={items} label={name} />
              ) : (
                <SimpleList ctx={ctx} path={here} items={items} label={name} />
              )}
              <Hint ctx={ctx} schema={sub} name={name} />
              <Errors codes={ctx.problems.get(field)} />
            </div>
          )
        }
        const locked = path.length === 0 && ctx.lockedKey.includes(name)
        return (
          <div key={name} className="field">
            <label className="field-label" htmlFor={`f-${field}`}>
              {label}
              {locked ? <span className="muted small"> {t('refPanelKeyLocked')}</span> : null}
            </label>
            <FieldInput
              id={`f-${field}`}
              kind={kind}
              schema={sub}
              value={getAt(ctx.draft, here)}
              readOnly={ctx.readOnly || locked}
              label={name}
              onChange={(next) => ctx.set(here, next)}
            />
            <Hint ctx={ctx} schema={sub} name={name} />
            <Errors codes={ctx.problems.get(field)} />
          </div>
        )
      })}
    </div>
  )
}

/**
 * Les champs d'un objet tirés de son schéma, sans le panneau autour : repris par
 * la fiche d'un objet de cartographie (ticket 6f71bcba). `names` restreint les
 * propriétés de premier niveau affichées ; `onChange` reçoit la valeur entière.
 * La description de chaque champ s'affiche sous lui. « Ouvrir » sur un élément
 * d'un tableau d'objets l'ouvre sur place, avec un fil d'Ariane pour revenir.
 */
export function SchemaFields({
  schema,
  draft,
  readOnly,
  problems,
  onChange,
  names,
  rootLabel,
}: {
  schema: Schema
  draft: Value
  readOnly: boolean
  problems: FieldProblems
  onChange: (next: Value) => void
  names?: string[]
  /** Premier maillon du fil d'Ariane quand un élément est ouvert. */
  rootLabel: string
}) {
  const { t } = useTranslation()
  const [path, setPath] = useState<PathPart[]>([])
  const ctx: Ctx = {
    draft,
    readOnly,
    lockedKey: [],
    problems,
    set: (at, next) => onChange(setAt(draft, at, next) as Value),
    open: setPath,
    describe: true,
  }
  if (path.length === 0) return <ObjectFields ctx={ctx} schema={schema} path={[]} names={names} />
  return (
    <div className="stack">
      <Crumbs crumbs={crumbsOf(rootLabel, path)} onOpen={setPath} label={t('refPanelPath')} />
      <ObjectFields ctx={ctx} schema={schemaAt(schema, path)} path={path} />
    </div>
  )
}

export function ValuePanel({
  model,
  draft,
  isNew,
  readOnly,
  problems,
  onChange,
  onApply,
  onCancel,
  onDelete,
}: {
  model: RefModel
  draft: Value
  isNew: boolean
  readOnly: boolean
  problems: FieldProblems
  onChange: (next: Value) => void
  onApply: () => void
  onCancel: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const [path, setPath] = useState<PathPart[]>([])
  const key = Array.isArray(model['x-key']) ? model['x-key'] : []
  const ctx: Ctx = {
    draft,
    readOnly,
    // Les champs de la clé ne bougent plus une fois la valeur créée.
    lockedKey: isNew ? [] : key,
    problems,
    set: (at, next) => onChange(setAt(draft, at, next) as Value),
    open: setPath,
  }
  const title = isNew ? t('refPanelNew') : t('refPanelTitle', { key: keyLabel(draft, key) })
  // Fil d'Ariane : « FR · site › paliers[2] › … ».
  const crumbs = crumbsOf(keyLabel(draft, key) || title, path)
  const whole = problems.get('')
  return (
    <aside role="dialog" aria-label={title} className="panel ref-panel stack">
      <div className="row dialog-head">
        <h3>{title}</h3>
        <button type="button" className="btn btn-sm" onClick={onCancel} aria-label={t('close')}>
          {'✕'}
        </button>
      </div>
      <Crumbs crumbs={crumbs} onOpen={setPath} label={t('refPanelPath')} />
      {whole ? (
        <p className="empty-note text-danger" role="alert">
          <Errors codes={whole} />
        </p>
      ) : null}
      <ObjectFields ctx={ctx} schema={schemaAt(model, path)} path={path} />
      {!readOnly ? (
        <div className="row modal-foot">
          <button type="button" className="btn btn-danger" onClick={onDelete}>
            {t('refPanelDelete')}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            {t('refPanelCancel')}
          </button>
          <button type="button" className="btn btn-primary" onClick={onApply}>
            {t('refPanelApply')}
          </button>
        </div>
      ) : null}
    </aside>
  )
}
