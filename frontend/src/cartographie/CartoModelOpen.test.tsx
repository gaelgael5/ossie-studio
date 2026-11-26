import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { CartoModelPage } from './CartoModelPage'
import type { ArchimateDelegates } from './urbanArchimate'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))
// Le vrai composant est servi par le backend : ici, un élément de remplacement qui
// garde ce qu'Ossie lui passe.
vi.mock('./urbanArchimate', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./urbanArchimate')>()),
  loadUrbanArchimate: () => Promise.resolve(),
}))

class FakeArchimate extends HTMLElement {
  static last: FakeArchimate | null = null
  delegates: ArchimateDelegates = {}
  readOnly = false
  loaded: Record<string, unknown> | null = null
  connectedCallback() {
    FakeArchimate.last = this
  }
  loadModel(model: Record<string, unknown>) {
    this.loaded = model
    return Promise.resolve({ warnings: [] })
  }
  save() {
    return SAVED
  }
}

beforeAll(() => {
  customElements.define('urban-archimate', FakeArchimate)
})

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const SAVED = {
  modele: { id: 'MOD-BOUTIQUE', regles: 'r', graphique: 'g', elements: ['APP-BOUTIQUE'] },
  graphique: { vues: { 'VUE-1': { noeuds: { N1: { x: 80, y: 60 } } } } },
  nouvellesRelations: [{ cle: 'REL-NOUVELLE', type: 'acces', source: 'A', cible: 'B' }],
}
const HYDRATED = { id: 'MOD-BOUTIQUE', regles: { chemin: 'r', data: {} }, graphique: { chemin: 'g', data: {} } }
const SUMMARY = {
  slug: 'boutique', id: 'MOD-BOUTIQUE', name: 'Boutique', description: '', path: 'p', graphic: 'g', rules: 'r',
  rules_name: 'standard', views: [], elements: 0, relationships: 0, keys: [], unused_documents: 0, errors: 0,
  preview: { boxes: [], links: [] }, dates: null, state: null, problems: [],
}

function setup(options: { capabilities?: string[]; refused?: boolean; saveRefused?: boolean } = {}) {
  const calls: string[] = []
  const puts: string[] = []
  const capabilities = options.capabilities ?? ['cartographie:read', 'cartographie:write']
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      calls.push(path)
      let status = 200
      let body: unknown = {}
      if (init?.method === 'PUT') {
        puts.push(String(init.body))
        if (options.saveRefused) {
          status = 409
          body = {
            error: 'carto_model_relation_refused',
            error_args: { key: 'REL-NOUVELLE', reason: 'object_key_taken', problems: [] },
          }
        } else body = { ...SUMMARY, revision: 'draft:3', created_relations: ['REL-NOUVELLE'] }
      } else if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path === `${BASE}/modeles/boutique`) body = SUMMARY
      else if (path === `${BASE}/modeles/boutique/hydrate` && options.refused) {
        status = 409
        body = {
          error: 'carto_model_invalid',
          error_args: { problems: [{ path: 'p', code: 'model_key_without_document', detail: 'APP-ABSENT', field: 'elements[0]' }] },
        }
      } else if (path === `${BASE}/modeles/boutique/hydrate`) body = HYDRATED
      else if (path.startsWith(`${BASE}/modeles/boutique/outils`)) {
        body = {
          groupes: [
            { id: 'application', libelle: 'Application', outils: [{ cle: 'APP-BOUTIQUE', libelle: 'Boutique en ligne', icone: '<svg/>' }] },
            { id: 'relations', libelle: '', outils: [{ cle: 'REL-1', libelle: 'Écrit (A → B)' }] },
          ],
        }
      } else if (path === `${BASE}/modeles/boutique/pose/OBJ-FACTURE`) {
        body = { objet: { chemin: 'c', data: { cle: 'OBJ-FACTURE', type: 'document', nom: 'Facture' } }, regles: { name: '$.nom' } }
      } else if (path.includes('/relations-permises')) {
        body = {
          archimate: ['Access', 'Association'],
          types: [
            { slug: 'acces', title: 'Accès', archimate: 'Access', object_schema: {}, type_schema: { title: 'Accès', 'x-archimate': 'Access' } },
            { slug: 'association', title: 'Association', archimate: 'Association', object_schema: {}, type_schema: { title: 'Association', 'x-archimate': 'Association' } },
          ],
        }
      }
      return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/cartographie/modeles/boutique']}>
        <Routes>
          <Route path="/cartographie/modeles/:slug" element={<CartoModelPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return Object.assign(calls, { puts })
}

afterEach(() => {
  vi.unstubAllGlobals()
  FakeArchimate.last = null
})

async function element(): Promise<FakeArchimate> {
  await waitFor(() => expect(FakeArchimate.last?.loaded).toBeTruthy())
  return FakeArchimate.last as FakeArchimate
}

describe('Cartographie — ouvrir un modèle (ticket f5421cc7)', () => {
  it('le modèle hydraté par Ossie est passé au composant, modifiable avec le droit (critère 1)', async () => {
    setup()
    expect(await screen.findByRole('heading', { name: 'Boutique' })).toBeInTheDocument()
    const diagram = await element()
    expect(diagram.loaded).toEqual(HYDRATED)
    expect(diagram.readOnly).toBe(false)
    expect(screen.getByRole('link', { name: '← Modèles' })).toHaveAttribute('href', '/cartographie/modeles')
  })

  it('boîte à outils : objets existants par type, relations existantes, filtrées par le point de vue (critère 2)', async () => {
    const calls = setup()
    const diagram = await element()
    const groups = await diagram.delegates.toolbox!({ pointDeVue: 'application-cooperation', vueId: 'VUE-1' })
    expect(calls).toContain(`${BASE}/modeles/boutique/outils?point_de_vue=application-cooperation`)
    expect(groups.map((g) => [g.id, g.libelle])).toEqual([
      ['application', 'Application'],
      ['relations', 'Relations existantes'],
    ])
  })

  it('délégué de pose : la clé part vers Ossie, l’objet et sa règle reviennent (critère 3)', async () => {
    const calls = setup()
    const diagram = await element()
    const dropped = await diagram.delegates.drop!('OBJ-FACTURE')
    expect(calls).toContain(`${BASE}/modeles/boutique/pose/OBJ-FACTURE`)
    expect(dropped.objet.data.nom).toBe('Facture')
    expect(dropped.regles).toEqual({ name: '$.nom' })
  })

  it('relations permises : les types de l’espace, au format du contrat (critère 4)', async () => {
    const calls = setup()
    const diagram = await element()
    const allowed = await diagram.delegates.allowedRelations!('PRC-PRISE-COMMANDE', 'OBJ-COMMANDE')
    expect(calls).toContain(`${BASE}/objets/PRC-PRISE-COMMANDE/relations-permises?cible=OBJ-COMMANDE`)
    expect(allowed).toEqual([
      { type: 'acces', archimate: 'Access', libelle: 'Accès', schema: { title: 'Accès', 'x-archimate': 'Access' } },
      { type: 'association', archimate: 'Association', libelle: 'Association', schema: { title: 'Association', 'x-archimate': 'Association' } },
    ])
  })

  it('sans le droit de modifier : diagramme en lecture seule (critère 7)', async () => {
    setup({ capabilities: ['cartographie:read'] })
    const diagram = await element()
    expect(diagram.readOnly).toBe(true)
    expect(screen.getByText('lecture seule')).toBeInTheDocument()
  })

  it('un modèle incohérent ne s’ouvre pas : le problème est nommé', async () => {
    setup({ refused: true })
    expect(await screen.findByText(/APP-ABSENT/)).toBeInTheDocument()
    expect(FakeArchimate.last).toBeNull()
  })

  it('enregistrer : ce que rend le composant part vers Ossie, puis le modèle est rechargé (ticket c3dd045c)', async () => {
    const calls = setup()
    const diagram = await element()
    const save = screen.getByRole('button', { name: 'Enregistrer' })
    expect(save).toBeDisabled()
    act(() => {
      diagram.dispatchEvent(new CustomEvent('changed', { detail: { scope: 'graphics' } }))
    })
    expect(await screen.findByText('modifié, non enregistré')).toBeInTheDocument()
    const hydrations = () => calls.filter((c) => c.endsWith('/hydrate')).length
    const before = hydrations()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.puts).toHaveLength(1))
    expect(JSON.parse(calls.puts[0])).toEqual(SAVED)
    await waitFor(() => expect(hydrations()).toBeGreaterThan(before))
    await waitFor(() => expect(screen.queryByText('modifié, non enregistré')).not.toBeInTheDocument())
  })

  it('une nouvelle relation refusée : l’erreur nomme sa clé, le dessin reste modifié', async () => {
    setup({ saveRefused: true })
    const diagram = await element()
    act(() => {
      diagram.dispatchEvent(new CustomEvent('changed', { detail: { scope: 'model' } }))
    })
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Enregistrer' }))
    expect(await screen.findByText(/REL-NOUVELLE/)).toBeInTheDocument()
    expect(screen.getByText('modifié, non enregistré')).toBeInTheDocument()
  })

  it('sans le droit de modifier : pas de bouton « Enregistrer »', async () => {
    setup({ capabilities: ['cartographie:read'] })
    await element()
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).not.toBeInTheDocument()
  })
})
