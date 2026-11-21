// Vue d'une branche choisie par la personne qui a un brouillon : « fusionnée »
// (la branche avec son brouillon, par défaut), « publiée » (sans le brouillon),
// « brouillon » (seulement ses objets). Préférence d'affichage, mémorisée par
// branche dans le navigateur ; sans effet sur le brouillon lui-même.

export type DraftViewMode = 'merged' | 'published' | 'draft'

const KEY = (branchId: string) => `ossie.view.${branchId}`
const BRANCH_PATH = /^\/api\/spaces\/[^/]+\/branches\/([^/?]+)\//

export function readDraftView(branchId: string | null): DraftViewMode {
  if (!branchId) return 'merged'
  try {
    const value = window.localStorage.getItem(KEY(branchId))
    return value === 'published' || value === 'draft' ? value : 'merged'
  } catch {
    return 'merged'
  }
}

export function writeDraftView(branchId: string, mode: DraftViewMode): void {
  try {
    window.localStorage.setItem(KEY(branchId), mode)
  } catch {
    // Stockage indisponible (navigation privée) : la préférence ne survit pas.
  }
}

/** Ajoute `view=published` aux lectures de la branche quand la vue publiée est
 * choisie : le serveur ignore alors le brouillon (les écritures n'en ont cure). */
export function withDraftView(path: string, method: string | undefined): string {
  if ((method ?? 'GET') !== 'GET') return path
  const match = BRANCH_PATH.exec(path)
  // Les routes du brouillon lui-même (…/branches/<id>/draft…) restent telles quelles.
  if (!match || /\/branches\/[^/?]+\/draft(?:[/?]|$)/.test(path)) return path
  if (readDraftView(match[1] ?? null) !== 'published') return path
  return `${path}${path.includes('?') ? '&' : '?'}view=published`
}
