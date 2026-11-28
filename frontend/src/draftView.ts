import { useSyncExternalStore } from 'react'

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

// Le choix se fait dans le menu du haut et se lit aussi dans la barre du
// brouillon (ticket e7e377b0) : un petit magasin notifie les deux.
const listeners = new Set<() => void>()

export function writeDraftView(branchId: string, mode: DraftViewMode): void {
  try {
    window.localStorage.setItem(KEY(branchId), mode)
  } catch {
    // Stockage indisponible (navigation privée) : la préférence ne survit pas.
  }
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Vue choisie pour la branche, relue à chaque changement. */
export function useDraftView(branchId: string | null): DraftViewMode {
  return useSyncExternalStore(
    subscribe,
    () => readDraftView(branchId),
    () => 'merged',
  )
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

// --- Première modification : retour en vue fusionnée (bug a512d8c3) ---------------------
// Gael : « quand l'utilisateur commence à faire ses premières modifications il faut
// automatiquement changer la vue en fusionné. » Toute écriture va dans le brouillon :
// en vue « Publiée » ou « Brouillon », l'écran ne montrerait pas ce sur quoi elle
// s'applique (un ajout se heurtait à un objet invisible). Dès qu'une écriture part
// vers une branche, la vue de cette branche repasse en « Fusionnée », et les écrans
// se relisent (les abonnés de `onAutoMerged`).

// Les POST qui ne modifient rien : aperçus, essais, prévisualisations.
const READ_ONLY_POST = /\/(apercu|essai|preview|resoudre)(?:\?|$)/
const autoMergedListeners = new Set<(branchId: string) => void>()

/** S'abonner au retour automatique en vue fusionnée (relire les écrans). */
export function onAutoMerged(listener: (branchId: string) => void): () => void {
  autoMergedListeners.add(listener)
  return () => autoMergedListeners.delete(listener)
}

/** Avant une écriture vers une branche : repasse sa vue en « Fusionnée » si besoin.
 * Rend vrai si la vue a changé. Les routes du brouillon lui-même (publier, fusionner,
 * abandonner) n'y touchent pas. */
export function mergeViewBeforeWrite(path: string, method: string | undefined): boolean {
  if ((method ?? 'GET') === 'GET' || READ_ONLY_POST.test(path)) return false
  const match = BRANCH_PATH.exec(path)
  if (!match || /\/branches\/[^/?]+\/draft(?:[/?]|$)/.test(path)) return false
  const branchId = match[1] ?? ''
  if (readDraftView(branchId) === 'merged') return false
  writeDraftView(branchId, 'merged')
  autoMergedListeners.forEach((l) => l(branchId))
  return true
}
