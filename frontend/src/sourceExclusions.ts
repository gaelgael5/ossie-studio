// Répertoires exclus d'une source Git (`parameters.exclusions` du manifeste).
// Même règle que la collecte (backend `sources/collect.py:is_excluded`) :
// - sans `/` : nom de dossier, exclu où qu'il soit (`tests`) ;
// - avec `/` : chemin depuis la racine du dépôt, avec son contenu ;
// - avec `*` : motif (non évalué ici : l'écran ne marque que les deux premiers).
// Les défauts (node_modules, vendor, .git) s'appliquent toujours en plus.

export const DEFAULT_EXCLUSIONS = ['node_modules', 'vendor', '.git']

export function exclusionsOf(parameters: Record<string, unknown> | undefined): string[] {
  const value = parameters?.exclusions
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

function normalize(entry: string): string {
  return entry
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\/+|\/+$/g, '')
}

/** L'exclusion qui couvre ce dossier (chemin dans le dépôt), ou null. */
export function excludingEntry(folder: string, exclusions: string[]): string | null {
  const parts = folder.split('/')
  for (const raw of [...exclusions, ...DEFAULT_EXCLUSIONS]) {
    const entry = normalize(raw)
    if (!entry || entry.includes('*') || entry.includes('?')) continue
    if (entry.includes('/')) {
      if (folder === entry || folder.startsWith(`${entry}/`)) return raw
    } else if (parts.includes(entry)) {
      return raw
    }
  }
  return null
}

export function withExclusion(exclusions: string[], folder: string): string[] {
  const entry = normalize(folder)
  return exclusions.some((e) => normalize(e) === entry) ? exclusions : [...exclusions, entry]
}

export function withoutExclusion(exclusions: string[], entry: string): string[] {
  const target = normalize(entry)
  return exclusions.filter((e) => normalize(e) !== target)
}

export function parseExclusionInput(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => normalize(s))
    .filter(Boolean)
}
