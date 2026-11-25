import { useSyncExternalStore } from 'react'

// Mode lecture de l'explorateur raw (ticket 4e287004) : quand la personne
// travaille dans l'explorateur, le haut de page (barre du brouillon, en-tête
// « Sources », onglets) se replie pour laisser toute la hauteur à l'arbre, aux
// vignettes et à l'aperçu.
//
// État partagé par plusieurs composants sans lien de parenté (Layout, page
// Sources, explorateur) : un petit magasin plutôt qu'un contexte, pour ne pas
// faire traverser l'état par toutes les pages. Gardé dans sessionStorage :
// un rechargement le conserve, une nouvelle visite (nouvel onglet) part déplié.
const KEY = 'ossie.readingMode'
const listeners = new Set<() => void>()

function read(): boolean {
  try {
    return sessionStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

let current = read()

export function setReadingMode(next: boolean): void {
  if (next === current) return
  current = next
  try {
    if (next) sessionStorage.setItem(KEY, '1')
    else sessionStorage.removeItem(KEY)
  } catch {
    // Stockage indisponible (navigation privée) : l'état vit seulement en mémoire.
  }
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useReadingMode(): boolean {
  return useSyncExternalStore(subscribe, () => current, () => false)
}

/** Pour les tests : repart d'un état déplié. */
export function resetReadingMode(): void {
  setReadingMode(false)
}
