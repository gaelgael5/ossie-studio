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
