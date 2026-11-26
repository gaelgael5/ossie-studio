// L'éditeur de processus `urban-bpmn` (paquet Python, version fixée dans
// backend/pyproject.toml), servi par l'application elle-même sous /api/bpmn (voir
// app.py), jamais par un CDN. Il s'enregistre seul (`<urban-bpmn>`) au chargement de
// son module. Les types ci-dessous reprennent ce dont Ossie se sert dans son contrat
// (`src/contract/types.ts` du dépôt urban-bpmn).

import type { CartoBpmnObject, CartoToolGroup } from '../apiClient'

export const BPMN_ASSETS = '/api/bpmn'
export const BPMN_TAG = 'urban-bpmn'

export type BpmnGenre = 'participant' | 'couloir' | 'objet-donnees' | 'stockage-donnees' | 'collaboration'

/** Ce que l'hôte rend à la pose d'un outil (`DropResult`). */
export interface BpmnDropResult {
  nom: string
  type?: string
  genre: BpmnGenre
  membres?: (CartoBpmnObject & { genre: BpmnGenre })[]
  avertissement?: { message: string; action?: { id: string; libelle: string } }
}

export interface BpmnDelegates {
  toolbox?: (context: { palette?: string }) => Promise<CartoToolGroup[]>
  drop?: (cle: string) => Promise<BpmnDropResult>
  action?: (cle: string, action: string) => Promise<void> | void
  attach?: (context: { elementId: string; type: string; cle?: string }) => Promise<CartoBpmnObject | null>
}

export interface BpmnElement extends HTMLElement {
  delegates: BpmnDelegates
  readOnly: boolean
  loadXml: (xml: string, options?: { references?: CartoBpmnObject[] }) => Promise<{ warnings: unknown[] }>
  save: () => Promise<{ xml: string }>
}

/** Chargé une fois pour toute la page. */
let loading: Promise<void> | null = null

export function loadUrbanBpmn(): Promise<void> {
  if (customElements.get(BPMN_TAG)) return Promise.resolve()
  if (loading === null) {
    loading = new Promise<void>((resolve, reject) => {
      const script = document.createElement('script')
      script.type = 'module'
      script.src = `${BPMN_ASSETS}/urban-bpmn.js`
      script.addEventListener('load', () => resolve())
      script.addEventListener('error', () => {
        loading = null
        reject(new Error('urban-bpmn introuvable'))
      })
      document.head.append(script)
    }).then(() => customElements.whenDefined(BPMN_TAG).then(() => undefined))
  }
  return loading
}
