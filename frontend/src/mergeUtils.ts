// Outils de la fenêtre de fusion : aplatir un objet en pointeurs JSON pour
// comparer propriété par propriété, et afficher une valeur.

export function flatten(value: unknown, pointer = ''): Map<string, string> {
  const out = new Map<string, string>()
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) out.set(pointer || '/', '{}')
    for (const [key, child] of entries) {
      const escaped = key.replace(/~/g, '~0').replace(/\//g, '~1')
      for (const [p, v] of flatten(child, `${pointer}/${escaped}`)) out.set(p, v)
    }
    return out
  }
  out.set(pointer || '/', show(value))
  return out
}

export function show(value: unknown): string {
  if (value === undefined || value === null) return '—'
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export interface DiffRow {
  pointer: string
  left: string
  right: string
}

/** Propriétés qui diffèrent entre deux objets (publié / brouillon). */
export function diffRows(left: unknown, right: unknown): DiffRow[] {
  const a = flatten(left)
  const b = flatten(right)
  const pointers = [...new Set([...a.keys(), ...b.keys()])].sort()
  return pointers
    .filter((p) => a.get(p) !== b.get(p))
    .map((p) => ({ pointer: p, left: a.get(p) ?? '—', right: b.get(p) ?? '—' }))
}

/** Valeur saisie librement : JSON si possible, sinon texte brut. */
export function parseFree(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

// --- Fenêtre en trois colonnes (feature d727680a) ----------------------------------

/** Valeur à un pointeur JSON : `present` faux si la clé n'existe pas. */
export interface At {
  present: boolean
  value: unknown
}

function parts(pointer: string): string[] {
  if (pointer === '' || pointer === '/') return []
  return pointer
    .slice(1)
    .split('/')
    .map((p) => p.replace(/~1/g, '/').replace(/~0/g, '~'))
}

export function getAt(doc: unknown, pointer: string): At {
  let node: unknown = doc
  for (const part of parts(pointer)) {
    if (node === null || typeof node !== 'object' || Array.isArray(node) || !(part in (node as object))) {
      return { present: false, value: undefined }
    }
    node = (node as Record<string, unknown>)[part]
  }
  return { present: doc !== undefined, value: node }
}

/** Copie de `doc` avec `value` au pointeur (`present` faux : clé retirée). */
export function setAt(doc: unknown, pointer: string, at: At): unknown {
  const keys = parts(pointer)
  if (keys.length === 0) return at.present ? at.value : undefined
  const root: Record<string, unknown> =
    doc !== null && typeof doc === 'object' && !Array.isArray(doc) ? { ...(doc as Record<string, unknown>) } : {}
  let node = root
  for (const key of keys.slice(0, -1)) {
    const child = node[key]
    const copy = child !== null && typeof child === 'object' && !Array.isArray(child) ? { ...(child as Record<string, unknown>) } : {}
    node[key] = copy
    node = copy
  }
  const last = keys[keys.length - 1]
  if (at.present) node[last] = at.value
  else delete node[last]
  return root
}

/** Égalité de contenu (clés dans n'importe quel ordre). */
export function sameAt(a: At, b: At): boolean {
  return a.present === b.present && (!a.present || stable(a.value) === stable(b.value))
}

function stable(value: unknown): string {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>).sort(([x], [y]) => x.localeCompare(y))
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(',')}}`
  }
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  return JSON.stringify(value) ?? 'undefined'
}

/** Les blocs d'une fusion : chaque propriété feuille des trois versions ; un conflit
 * qui porte sur un objet entier reste un seul bloc. Triés. */
export function mergeBlocks(conflicts: string[], ...docs: unknown[]): string[] {
  const leaves = new Set<string>()
  for (const doc of docs) for (const p of flatten(doc).keys()) leaves.add(p)
  const under = (p: string) => conflicts.find((c) => c !== p && (c === '' || p.startsWith(`${c}/`)))
  const out = new Set<string>(conflicts)
  for (const p of leaves) if (!under(p)) out.add(p)
  return [...out].sort()
}

/** Texte affiché et modifiable d'une valeur : une chaîne telle quelle, le reste en JSON. */
export function editText(at: At): string {
  if (!at.present) return ''
  return typeof at.value === 'string' ? at.value : JSON.stringify(at.value)
}

/** Lit une saisie selon le type attendu (celui des versions publiée et du brouillon).
 * Rend la valeur, ou `error` si la saisie ne convient pas au type. */
export function readEdit(text: string, expected: unknown[]): { value?: unknown; error?: 'number' | 'boolean' | 'json' } {
  const kinds = new Set(expected.filter((v) => v !== undefined && v !== null).map((v) => (Array.isArray(v) ? 'array' : typeof v)))
  const kind = kinds.size === 1 ? [...kinds][0] : null
  if (kind === 'string') return { value: text } // vider donne une valeur vide, jamais une suppression
  if (kind === 'number') {
    const n = Number(text)
    return text.trim() !== '' && Number.isFinite(n) ? { value: n } : { error: 'number' }
  }
  if (kind === 'boolean') {
    if (text.trim() === 'true') return { value: true }
    if (text.trim() === 'false') return { value: false }
    return { error: 'boolean' }
  }
  if (kind === 'object' || kind === 'array') {
    try {
      const value = JSON.parse(text) as unknown
      const ok = kind === 'array' ? Array.isArray(value) : value !== null && typeof value === 'object' && !Array.isArray(value)
      return ok ? { value } : { error: 'json' }
    } catch {
      return { error: 'json' }
    }
  }
  return { value: parseFree(text) }
}
