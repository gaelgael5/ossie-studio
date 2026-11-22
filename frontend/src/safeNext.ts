/**
 * Chemin où revenir après la connexion, s'il reste dans l'application : un
 * chemin local (`/…`), jamais une autre origine (`//hôte`, `https://…`). Miroir
 * de `safe_next` côté serveur. Sert par exemple à revenir sur l'écran de
 * consentement d'un client MCP après s'être connecté.
 */
export function safeNext(value: string | null | undefined): string | null {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f]/.test(value)) return null
  return value
}

/** Adresse de la page de connexion qui ramènera ensuite à `path`. */
export function loginWithNext(path: string): string {
  return `/login?next=${encodeURIComponent(path)}`
}
