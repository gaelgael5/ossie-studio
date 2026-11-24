import type { RefModel } from './modelRules'

/** Un schéma de propriété du modèle (sous-ensemble de JSON Schema). */
export type Schema = Record<string, unknown>
export type Value = Record<string, unknown>
export type PathPart = string | number

export type Kind =
  | 'string'
  | 'enum'
  | 'date'
  | 'datetime'
  | 'integer'
  | 'number'
  | 'boolean'
  | 'object'
  | 'array'
  | 'unknown'

// Même séparateur que le serveur (`referentiels/model.py`, SEPARATOR).
export const SEPARATOR = ' › '

export function kindOf(schema: Schema | undefined): Kind {
  const type = schema?.type
  if (type === 'string') {
    if (Array.isArray(schema?.enum)) return 'enum'
    if (schema?.format === 'date') return 'date'
    if (schema?.format === 'date-time') return 'datetime'
    return 'string'
  }
  if (type === 'integer' || type === 'number' || type === 'boolean' || type === 'object' || type === 'array') {
    return type
  }
  return 'unknown'
}

export function isSimple(schema: Schema | undefined): boolean {
  return !['object', 'array', 'unknown'].includes(kindOf(schema))
}

export function propertiesOf(schema: Schema | undefined): [string, Schema][] {
  const props = schema?.properties
  return props && typeof props === 'object' ? (Object.entries(props) as [string, Schema][]) : []
}

export function requiredOf(schema: Schema | undefined): string[] {
  return Array.isArray(schema?.required) ? (schema.required as string[]) : []
}

export function itemsOf(schema: Schema | undefined): Schema | undefined {
  const items = schema?.items
  return items && typeof items === 'object' ? (items as Schema) : undefined
}

/** La clé d'une valeur pour l'affichage : « FR · site » (vide si incomplète). */
export function keyLabel(value: Value, key: string[]): string {
  const parts = key.map((k) => value[k])
  if (parts.some((p) => p === undefined || p === null || p === '')) return ''
  return parts.map(String).join(' · ')
}

function sortPart(part: unknown): [number, string | number] {
  if (typeof part === 'boolean') return [0, part ? 1 : 0]
  if (typeof part === 'number') return [1, part]
  if (part === undefined || part === null) return [3, '']
  return [2, String(part)]
}

/** Comparaison par clé, comme le serveur (booléens, nombres, textes ; clé
 * incomplète en dernier). */
export function compareByKey(a: Value, b: Value, key: string[]): number {
  for (const name of key) {
    const [ra, va] = sortPart(a[name])
    const [rb, vb] = sortPart(b[name])
    if (ra !== rb) return ra - rb
    if (va < vb) return -1
    if (va > vb) return 1
  }
  return 0
}

export function shown(value: unknown): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

export function matchesSearch(value: Value, columns: string[], query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return columns.some((c) => shown(value[c]).toLowerCase().includes(q))
}

export function matchesFilter(value: Value, property: string, expected: string): boolean {
  if (!property || expected === '') return true
  return shown(value[property]) === expected
}

/** Le chemin d'un champ dans une valeur, comme le serveur : « paliers[2] › delai_jours ». */
export function fieldPath(path: PathPart[]): string {
  let out = ''
  for (const part of path) {
    if (typeof part === 'number') out += `[${part}]`
    else out += out ? `${SEPARATOR}${part}` : part
  }
  return out
}

export function getAt(value: unknown, path: PathPart[]): unknown {
  let current: unknown = value
  for (const part of path) {
    if (current === null || typeof current !== 'object') return undefined
    current = (current as Record<string | number, unknown>)[part]
  }
  return current
}

/** Une copie de `value` où le champ `path` vaut `next` (`undefined` le retire). */
export function setAt(value: unknown, path: PathPart[], next: unknown): unknown {
  if (path.length === 0) return next
  const [head, ...rest] = path
  if (typeof head === 'number') {
    const list = Array.isArray(value) ? [...value] : []
    list[head] = setAt(list[head], rest, next)
    return list
  }
  const object: Record<string, unknown> = value && typeof value === 'object' && !Array.isArray(value) ? { ...(value as Value) } : {}
  const child = setAt(object[head], rest, next)
  if (child === undefined) delete object[head]
  else object[head] = child
  return object
}

/** Le schéma au bout d'un chemin (les rangs de tableau descendent dans `items`). */
export function schemaAt(model: RefModel | Schema, path: PathPart[]): Schema | undefined {
  let current: Schema | undefined = model as Schema
  for (const part of path) {
    if (typeof part === 'number') current = itemsOf(current)
    else current = propertiesOf(current).find(([name]) => name === part)?.[1]
    if (current === undefined) return undefined
  }
  return current
}

/** Lecture d'une saisie selon le type : `undefined` si vide (le champ est retiré). */
export function parseInput(kind: Kind, raw: string): unknown {
  if (raw === '') return undefined
  if (kind === 'integer') {
    const n = Number(raw)
    return Number.isInteger(n) ? n : raw
  }
  if (kind === 'number') {
    const n = Number(raw)
    return Number.isFinite(n) ? n : raw
  }
  if (kind === 'boolean') return raw === 'true'
  return raw
}

/** Un élément vide pour un tableau : objet pour un tableau d'objets, rien sinon. */
export function emptyItem(items: Schema | undefined): unknown {
  return kindOf(items) === 'object' ? {} : undefined
}

export function move<T>(list: T[], from: number, to: number): T[] {
  const next = [...list]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}
