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
