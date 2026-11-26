// Le composant de diagramme `urban-archimate` (paquet Python 0.1.1), servi par
// l'application elle-même sous /api/archimate (voir app.py), jamais par un CDN.
// Il s'enregistre seul (`<urban-archimate>`) au chargement de son module.
// Les types ci-dessous reprennent ce dont Ossie se sert dans son contrat
// (`src/contract/types.ts` du dépôt urban-archimate).

import type { CartoDropResult, CartoToolGroup } from '../apiClient'

export const ARCHIMATE_ASSETS = '/api/archimate'
export const ARCHIMATE_TAG = 'urban-archimate'

export interface AllowedRelationType {
  type: string
  archimate: string
  libelle?: string
  schema?: Record<string, unknown>
}

export interface ArchimateDelegates {
  toolbox?: (context: { pointDeVue?: string; vueId?: string }) => Promise<CartoToolGroup[]>
  drop?: (cle: string) => Promise<CartoDropResult>
  allowedRelations?: (cleSource: string, cleCible: string) => Promise<AllowedRelationType[]>
}

/** Ce que rend l'enregistrement : ce que l'hôte écrit (ticket c3dd045c). */
export interface ArchimateSaveResult {
  modele: Record<string, unknown>
  graphique: Record<string, unknown>
  nouvellesRelations: Record<string, unknown>[]
}

export interface ArchimateElement extends HTMLElement {
  delegates: ArchimateDelegates
  readOnly: boolean
  loadModel: (model: Record<string, unknown>) => Promise<{ warnings: unknown[] }>
  save: () => ArchimateSaveResult
}

/** Chargé une fois pour toute la page. */
let loading: Promise<void> | null = null

export function loadUrbanArchimate(): Promise<void> {
  if (customElements.get(ARCHIMATE_TAG)) return Promise.resolve()
  if (loading === null) {
    loading = new Promise<void>((resolve, reject) => {
      const script = document.createElement('script')
      script.type = 'module'
      script.src = `${ARCHIMATE_ASSETS}/urban-archimate.js`
      script.addEventListener('load', () => resolve())
      script.addEventListener('error', () => {
        loading = null
        reject(new Error('urban-archimate introuvable'))
      })
      document.head.append(script)
    }).then(() => customElements.whenDefined(ARCHIMATE_TAG).then(() => undefined))
  }
  return loading
}
