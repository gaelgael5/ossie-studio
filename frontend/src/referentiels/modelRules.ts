import { YAMLParseError, parse, stringify } from 'yaml'

/** Modèle d'un référentiel : un JSON Schema de type objet, clé sous `x-key`. */
export type RefModel = Record<string, unknown> & {
  properties?: Record<string, Record<string, unknown>>
  required?: string[]
  'x-key'?: string[]
}

/** Le modèle lu dans le YAML, ou pourquoi il ne se lit pas (ligne et colonne). */
export type Parsed = { ok: true; model: RefModel } | { ok: false; message: string }

const SIMPLE_TYPES = new Set(['string', 'integer', 'number', 'boolean'])

export function parseModel(text: string): Parsed {
  try {
    const value: unknown = parse(text)
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      return { ok: false, message: 'object expected' }
    }
    return { ok: true, model: value as RefModel }
  } catch (err) {
    if (err instanceof YAMLParseError) {
      const at = err.linePos?.[0]
      return { ok: false, message: at ? `${at.line}:${at.col} ${err.code}` : err.message }
    }
    return { ok: false, message: String(err) }
  }
}

/** Le modèle en YAML, dans l'ordre de ses clés (comme le serveur). */
export function modelText(model: RefModel): string {
  return stringify(model, { lineWidth: 0 })
}

export function modelKey(model: RefModel): string[] {
  return Array.isArray(model['x-key']) ? model['x-key'].filter((k) => typeof k === 'string') : []
}

/** Propriétés de premier niveau qui peuvent entrer dans la clé : simples (chaîne,
 * date, énumération, entier, nombre, booléen), jamais un objet ou un tableau. */
export function simpleProperties(model: RefModel): string[] {
  return Object.entries(model.properties ?? {})
    .filter(([, schema]) => SIMPLE_TYPES.has(String(schema?.type ?? '')))
    .map(([name]) => name)
}

/** La clé posée : liste ordonnée de propriétés simples, toutes obligatoires. */
export function withKey(model: RefModel, key: string[]): RefModel {
  const required = [...(model.required ?? [])]
  for (const name of key) if (!required.includes(name)) required.push(name)
  return { ...model, 'x-key': key, required }
}

export type KeyViolation = { kind: 'removed' | 'optional' | 'not_simple'; property: string }

/** Une modification qui retirerait une propriété de la clé, la rendrait
 * facultative, ou en ferait un objet ou un tableau : la première violation, ou null. */
export function keyViolation(next: RefModel, key: string[]): KeyViolation | null {
  const properties = next.properties ?? {}
  const required = next.required ?? []
  for (const property of key) {
    const schema = properties[property]
    if (schema === undefined) return { kind: 'removed', property }
    if (!SIMPLE_TYPES.has(String(schema?.type ?? ''))) return { kind: 'not_simple', property }
    if (!required.includes(property)) return { kind: 'optional', property }
  }
  return null
}

/** Ce que l'éditeur visuel renvoie, avec la clé et les annotations de premier
 * niveau du modèle courant : la clé se règle dans le bandeau, jamais dans l'éditeur
 * (qui ignore `x-key`). */
export function fromVisual(current: RefModel, visual: Record<string, unknown>): RefModel {
  const next: RefModel = { ...visual }
  if (current['x-key'] !== undefined) next['x-key'] = current['x-key']
  for (const annotation of ['title', 'description'] as const) {
    if (next[annotation] === undefined && current[annotation] !== undefined) {
      next[annotation] = current[annotation]
    }
  }
  return next
}
