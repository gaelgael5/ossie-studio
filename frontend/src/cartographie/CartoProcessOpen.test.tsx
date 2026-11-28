import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { CartoProcessPage } from './CartoProcessPage'
import type { BpmnDelegates } from './urbanBpmn'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))
// Le vrai composant est servi par le backend : ici, un élément de remplacement qui
// garde ce qu'Ossie lui passe.
vi.mock('./urbanBpmn', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./urbanBpmn')>()),
  loadUrbanBpmn: () => Promise.resolve(),
}))

class FakeBpmn extends HTMLElement {
  static last: FakeBpmn | null = null
  delegates: BpmnDelegates = {}
  readOnly = false
  loaded: { xml: string; references: unknown } | null = null
  connectedCallback() {
    FakeBpmn.last = this
  }
  loadXml(xml: string, options: { references?: unknown } = {}) {
    this.loaded = { xml, references: options.references }
    return Promise.resolve({ warnings: [] })
  }
  save() {
    return Promise.resolve({ xml: SAVED_XML })
  }
}

beforeAll(() => {
  customElements.define('urban-bpmn', FakeBpmn)
})

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const XML = '<bpmn:definitions id="PRC-VENTE"/>'
const SAVED_XML = '<bpmn:definitions id="PRC-VENTE"><!-- modifié --></bpmn:definitions>'
const REFERENCES = [{ cle: 'OBJ-COMMANDE', nom: 'Commande client', type: 'objet-metier' }]
const ICON = '<svg viewBox="0 0 16 16"></svg>'

function summary(detail: boolean) {
  return {
    key: 'PRC-VENTE', name: 'Vente — détail', path: 'cartographie/processus/prc-vente.bpmn', errors: 0, readable: true,
    detail_of: detail ? { key: 'PRC-PRISE-COMMANDE', ref: 'processus-metier/PRC-PRISE-COMMANDE', name: 'Prise de commande', exists: true } : null,
    participants: 0, lanes: 0, attached: 0, pools: [], preview: { boxes: [], links: [] }, dates: null, state: null,
    problems: [],
  }
}
const OBJECTS = [
  ['PRC-PRISE-COMMANDE', 'Prise de commande', 'BusinessProcess', 'processus-metier', 'Processus métier'],
  ['ACT-CLIENT', 'Client', 'BusinessActor', 'acteur', 'Acteur'],
  ['ROL-VENDEUR', 'Vendeur', 'BusinessRole', 'role', 'Rôle'],
].map(([key, name, archimate, type, type_title]) => ({
  key, name, archimate, type, ref: `${type}/${key}`, type_title, excerpt: '', properties: [], relations: 0, is_relation: false,
  source: null, target: null, path: 'p', dates: null, state: null,
}))
const CATALOG = {
  archimate: '3.2', layers: [], base: [],
  types: [
    { name: 'BusinessActor', kind: 'element', label: 'Acteur', layer: 'business', icon: ICON, attributes: [] },
    { name: 'BusinessProcess', kind: 'element', label: 'Processus', layer: 'business', icon: ICON, attributes: [] },
    { name: 'Assignment', kind: 'relation', label: 'Affectation', layer: '', icon: ICON, attributes: [], verbs: { out: 'est affecté à', in: 'a pour acteur' } },
  ],
}
const DROPS: Record<string, unknown> = {
  'ROL-VENDEUR': { nom: 'Vendeur', type: 'role', genre: 'couloir' },
  'ACT-CLIENT': {
    nom: 'Client', type: 'acteur', genre: 'participant',
    avertissement: {
      code: 'process_drop_not_linked', relation: 'Assignment', source: 'acteur/ACT-CLIENT', source_name: 'Client',
      target: 'processus-metier/PRC-PRISE-COMMANDE', target_name: 'Prise de commande',
    },
  },
  'COL-VENTE-EN-LIGNE': {
    nom: 'Vente en ligne', type: 'collaboration', genre: 'collaboration',
    membres: [
      { cle: 'ACT-CLIENT', nom: 'Client', type: 'acteur', genre: 'participant' },
      { cle: 'ORG-BOUTIQUE', nom: 'Boutique', type: 'acteur', genre: 'participant' },
    ],
  },
}

type Call = { method: string; path: string; body?: string }

function setup(options: { capabilities?: string[]; detail?: boolean; saveRefused?: boolean } = {}) {
  const calls: Call[] = []
  const capabilities = options.capabilities ?? ['cartographie:read', 'cartographie:write']
  let detail = options.detail ?? true
  FakeBpmn.last = null
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let status = 200
      let body: unknown = {}
      const p = path.split('?')[0]
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (p === `${BASE}/catalogue`) body = CATALOG
      else if (p === `${BASE}/objets` && method === 'GET') body = { readonly: false, objects: OBJECTS }
      else if (p === `${BASE}/objets/acteur/ACT-CLIENT/relations-permises`) {
        body = {
          archimate: ['Assignment', 'Association'],
          types: [
            { slug: 'association', title: 'Association', archimate: 'Association', object_schema: { type: 'object' }, type_schema: {} },
            { slug: 'affectation', title: 'Affectation', archimate: 'Assignment', object_schema: { type: 'object' }, type_schema: {} },
          ],
        }
      } else if (p === `${BASE}/processus/PRC-VENTE` && method === 'PUT') {
        if (options.saveRefused) {
          status = 400
          body = {
            error: 'carto_process_invalid',
            error_args: { key: 'PRC-VENTE', problems: [{ code: 'process_reference_unknown', detail: 'ACT-ABSENT', field: 'Lane_1' }] },
          }
        } else body = { ...summary(detail), revision: 'draft:2' }
      } else if (p === `${BASE}/processus/PRC-VENTE/rattacher`) {
        detail = true
        body = { ...summary(true), revision: 'draft:2' }
      } else if (p === `${BASE}/processus/PRC-VENTE`) body = summary(detail)
      else if (p === `${BASE}/processus/PRC-VENTE/contenu`) body = { key: 'PRC-VENTE', content: XML, references: REFERENCES }
      else if (p === `${BASE}/processus/PRC-VENTE/outils`) {
        body = {
          detail_of: 'PRC-PRISE-COMMANDE',
          groupes: [
            { id: 'lies', libelle: '', outils: [{ cle: 'ROL-VENDEUR', libelle: 'Vendeur', icone: ICON }] },
            { id: 'autres', libelle: '', outils: [{ cle: 'ACT-CLIENT', libelle: 'Client', icone: ICON }] },
          ],
        }
      } else if (p.startsWith(`${BASE}/processus/PRC-VENTE/pose/`)) body = DROPS[decodeURIComponent(p.split('/').at(-1)!)]
      else if (p === `${BASE}/processus/PRC-VENTE/rattachables`) {
        body = { element: 'bpmn:Lane', objets: [{ cle: 'ROL-LIVREUR', nom: 'Livreur', type: 'role' }, { cle: 'ROL-VENDEUR', nom: 'Vendeur', type: 'role' }] }
      } else if (p === `${BASE}/objets` && method === 'POST') body = { key: 'REL-CLIENT-PRISE-COMMANDE' }
      return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/cartographie/processus/PRC-VENTE']}>
        <Routes>
          <Route path="/cartographie/processus/:key" element={<CartoProcessPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

async function editor(): Promise<FakeBpmn> {
  await waitFor(() => expect(FakeBpmn.last?.loaded ?? null).not.toBeNull())
  return FakeBpmn.last!
}

describe('Cartographie — ouvrir et enregistrer un processus (ticket 48335b93)', () => {
  it('le composant reçoit le XML tel quel et les objets référencés avec leur nom à jour (critère 7)', async () => {
    setup()
    const element = await editor()
    expect(element.loaded).toEqual({ xml: XML, references: REFERENCES })
    expect(element.readOnly).toBe(false)
    expect(screen.getByRole('heading', { name: 'Vente — détail' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Prise de commande' })).toHaveAttribute('href', '/cartographie/objets/processus-metier/PRC-PRISE-COMMANDE')
  })

  it('boîte à outils : « Liés au processus » puis « Autres objets de l’espace » (critère 1)', async () => {
    setup()
    const element = await editor()
    const groups = await element.delegates.toolbox!({})
    expect(groups.map((g) => [g.id, g.libelle])).toEqual([
      ['lies', 'Liés au processus'],
      ['autres', 'Autres objets de l’espace'],
    ])
    expect(groups[0].outils[0]).toEqual({ cle: 'ROL-VENDEUR', libelle: 'Vendeur', icone: ICON })
  })

  it('poser un rôle affecté : un couloir, sans avertissement (critère 1)', async () => {
    setup()
    const element = await editor()
    expect(await element.delegates.drop!('ROL-VENDEUR')).toEqual({ nom: 'Vendeur', type: 'role', genre: 'couloir' })
  })

  it('poser un acteur non affecté : avertissement et « Créer la relation » pré-remplie (critère 2)', async () => {
    const calls = setup()
    const element = await editor()
    const dropped = await element.delegates.drop!('ACT-CLIENT')
    expect(dropped.genre).toBe('participant')
    expect(dropped.avertissement?.message).toBe('Client n’est pas affecté à Prise de commande')
    expect(dropped.avertissement?.action?.libelle).toBe('Créer la relation')
    await act(async () => {
      await element.delegates.action!('ACT-CLIENT', dropped.avertissement!.action!.id)
    })
    const dialog = await screen.findByRole('dialog', { name: 'Nouvelle relation' })
    // Les deux extrémités et l'Affectation, choisies d'office.
    expect(within(dialog).getByText('ACT-CLIENT')).toBeInTheDocument()
    expect(within(dialog).getAllByText('PRC-PRISE-COMMANDE').length).toBeGreaterThan(0)
    await waitFor(() => expect(within(dialog).getByText(/^Affectation/, { selector: '.carto-arrow span' })).toBeInTheDocument())
    expect(calls.some((c) => c.path.includes('/objets/acteur/ACT-CLIENT/relations-permises?cible=processus-metier%2FPRC-PRISE-COMMANDE'))).toBe(true)
  })

  it('poser une collaboration : ses membres passent au composant ; elle compte ensuite pour la pose (critère 3)', async () => {
    const calls = setup()
    const element = await editor()
    const dropped = await element.delegates.drop!('COL-VENTE-EN-LIGNE')
    expect(dropped.genre).toBe('collaboration')
    expect(dropped.membres?.map((m) => [m.cle, m.genre])).toEqual([
      ['ACT-CLIENT', 'participant'],
      ['ORG-BOUTIQUE', 'participant'],
    ])
    await element.delegates.drop!('ACT-CLIENT')
    expect(calls.some((c) => c.path.endsWith('/pose/ACT-CLIENT?collaboration=COL-VENTE-EN-LIGNE'))).toBe(true)
  })

  it('rattacher un élément du dessin : Ossie propose les objets du bon genre', async () => {
    const calls = setup()
    const element = await editor()
    let chosen: unknown = undefined
    act(() => {
      void Promise.resolve(element.delegates.attach!({ elementId: 'Lane_1', type: 'bpmn:Lane' })).then((value) => {
        chosen = value
      })
    })
    const dialog = await screen.findByRole('dialog', { name: 'Rattacher l’élément à un objet' })
    expect(calls.some((c) => c.path.endsWith('/rattachables?element=bpmn%3ALane'))).toBe(true)
    const user = userEvent.setup()
    await user.click(within(dialog).getByRole('option', { name: /Vendeur/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Rattacher' }))
    await waitFor(() => expect(chosen).toEqual({ cle: 'ROL-VENDEUR', nom: 'Vendeur', type: 'role' }))
  })

  it('enregistrer : le XML rendu par le composant est écrit', async () => {
    const calls = setup()
    const element = await editor()
    const user = userEvent.setup()
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
    act(() => {
      element.dispatchEvent(new CustomEvent('changed', { detail: { scope: 'model' } }))
    })
    expect(screen.getByText('modifié, non enregistré')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)).toEqual({ content: SAVED_XML })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled())
  })

  it('enregistrement refusé : la référence en cause est nommée', async () => {
    setup({ saveRefused: true })
    const element = await editor()
    act(() => {
      element.dispatchEvent(new CustomEvent('changed', { detail: { scope: 'model' } }))
    })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Enregistrer' }))
    expect(await screen.findByText(/ACT-ABSENT/)).toBeInTheDocument()
  })

  it('processus non rattaché : Ossie propose de le rattacher ; « Continuer sans » ouvre l’éditeur (critère 6)', async () => {
    setup({ detail: false })
    const prompt = await screen.findByRole('region', { name: 'Rattacher ce processus à un processus métier' })
    expect(FakeBpmn.last).toBeNull()
    // Seuls les processus métier sont proposés.
    const select = within(prompt).getByLabelText('Détail du processus métier')
    expect(within(select).queryByRole('option', { name: /Client/ })).not.toBeInTheDocument()
    await userEvent.setup().click(within(prompt).getByRole('button', { name: 'Continuer sans' }))
    await editor()
  })

  it('processus non rattaché : « Rattacher et ouvrir » écrit le rattachement puis ouvre', async () => {
    const calls = setup({ detail: false })
    const prompt = await screen.findByRole('region', { name: 'Rattacher ce processus à un processus métier' })
    const user = userEvent.setup()
    await waitFor(() => expect(within(prompt).getByRole('option', { name: /Prise de commande/ })).toBeInTheDocument())
    await user.selectOptions(within(prompt).getByLabelText('Détail du processus métier'), 'processus-metier/PRC-PRISE-COMMANDE')
    await user.click(within(prompt).getByRole('button', { name: 'Rattacher et ouvrir' }))
    await editor()
    const attach = calls.find((c) => c.path.endsWith('/rattacher'))
    expect(JSON.parse(attach!.body!)).toEqual({ detail_of: 'processus-metier/PRC-PRISE-COMMANDE' })
  })

  it('sans le droit de modifier : lecture seule, sans demande de rattachement (critère 8)', async () => {
    setup({ capabilities: ['cartographie:read'], detail: false })
    const element = await editor()
    expect(element.readOnly).toBe(true)
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Rattacher ce processus à un processus métier' })).not.toBeInTheDocument()
  })
})
