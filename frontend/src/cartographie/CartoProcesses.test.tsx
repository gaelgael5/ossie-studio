import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CartographiePage } from '../pages/CartographiePage'
import { CartoProcessPage } from './CartoProcessPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const PREVIEW = {
  boxes: [
    { x: 0, y: 0, l: 600, h: 200, kind: 'pool', label: 'Boutique' },
    { x: 60, y: 80, l: 36, h: 36, kind: 'event', label: '' },
    { x: 140, y: 60, l: 100, h: 80, kind: 'task', label: 'Saisir' },
    { x: 290, y: 75, l: 50, h: 50, kind: 'gateway', label: '' },
  ],
  links: [{ points: [[96, 98], [140, 98]] }],
}

function process(key: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    key,
    name,
    path: `cartographie/processus/${key.toLowerCase()}.bpmn`,
    errors: 0,
    readable: true,
    detail_of: null,
    participants: 1,
    lanes: 2,
    attached: 0,
    pools: ['Service après-vente'],
    preview: PREVIEW,
    dates: { created_at: '2026-11-25T10:00:00+00:00', created_by: 'camille.d', updated_at: '2026-11-25T10:00:00+00:00', updated_by: 'camille.d' },
    state: null,
    ...extra,
  }
}

const PROCESSES = [
  process('PRC-PRISE-COMMANDE-DETAIL', 'Prise de commande — détail', {
    detail_of: { key: 'PRC-PRISE-COMMANDE', ref: 'processus-metier/PRC-PRISE-COMMANDE', name: 'Prise de commande', exists: true },
    participants: 2,
    attached: 4,
    pools: ['Client', 'Boutique'],
  }),
  process('PRC-RETOURS', 'Traitement des retours'),
]
const OBJECTS = [
  { key: 'PRC-PRISE-COMMANDE', name: 'Prise de commande', archimate: 'BusinessProcess' },
  { key: 'PRC-RETOURS-METIER', name: 'Retours', archimate: 'BusinessProcess' },
  { key: 'ROL-VENDEUR', name: 'Vendeur', archimate: 'BusinessRole' },
].map((o) => ({ ...o, type: 'processus-metier', ref: `processus-metier/${o.key}`, type_title: 'T', excerpt: '', properties: [], relations: 0, is_relation: false, source: null, target: null, path: 'p', dates: null, state: null }))

const CHECK = {
  valid: true,
  error: null,
  has_diagram: true,
  participants: 1,
  lanes: 2,
  elements: 9,
  flows: 8,
  attached: 0,
  name: 'Traitement des retours',
  suggested_key: 'PRC-TRAITEMENT-DES-RETOURS',
  preview: PREVIEW,
}

function setup(options: { capabilities?: string[]; at?: string; check?: Record<string, unknown> } = {}) {
  const calls: { method: string; path: string; body?: string }[] = []
  const capabilities = options.capabilities ?? ['cartographie:read', 'cartographie:write']
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path === `${BASE}/catalogue`) body = { archimate: '3.2', layers: [], base: [], types: [] }
      else if (path === `${BASE}/types`) body = { readonly: false, types: [] }
      else if (path === `${BASE}/objets`) body = { readonly: false, objects: OBJECTS }
      else if (path === `${BASE}/modeles`) body = { readonly: false, models: [], rules: [] }
      else if (path === `${BASE}/processus` && method === 'GET') body = { readonly: false, processes: PROCESSES }
      else if (path === `${BASE}/processus/verifier`) body = options.check ?? CHECK
      else if (path === `${BASE}/processus/importer` || (path === `${BASE}/processus` && method === 'POST')) {
        const req = JSON.parse(String(init?.body)) as { key: string; name: string }
        body = { ...process(req.key, req.name), state: 'created', revision: 'draft:1' }
      } else if (path.startsWith(`${BASE}/processus/`)) body = { ...PROCESSES[1], problems: [] }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[options.at ?? '/cartographie/processus']}>
        <Routes>
          <Route path="/cartographie/processus" element={<CartographiePage />} />
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

function card(name: string): HTMLElement {
  return screen.getByRole('link', { name }).closest('article') as HTMLElement
}

describe('Cartographie — écran Processus (ticket 7e061c3e)', () => {
  it('l’écran Processus en vignettes : aperçu, détail de, non rattaché, nombres', async () => {
    setup()
    await screen.findByRole('link', { name: 'Prise de commande — détail' })
    expect(screen.getByRole('heading', { level: 1, name: 'Processus' })).toBeInTheDocument()
    const detail = card('Prise de commande — détail')
    expect(within(detail).getByText('PRC-PRISE-COMMANDE-DETAIL')).toBeInTheDocument()
    expect(within(detail).getByRole('link', { name: 'Prise de commande' })).toHaveAttribute(
      'href',
      '/cartographie/objets/processus-metier/PRC-PRISE-COMMANDE',
    )
    expect(within(detail).getByText('2 participants')).toBeInTheDocument()
    expect(within(detail).getByText('4 objets rattachés')).toBeInTheDocument()
    expect(detail.querySelectorAll('.carto-bpmn-shape')).toHaveLength(4)
    const retours = card('Traitement des retours')
    expect(within(retours).getByText('Non rattaché à un processus métier')).toBeInTheDocument()
    expect(within(retours).getByRole('button', { name: 'Rattacher…' })).toBeInTheDocument()
  })

  it('filtre « Non rattachés » et participant (critère 4)', async () => {
    setup()
    await screen.findByRole('link', { name: 'Prise de commande — détail' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Non rattachés' }))
    expect(screen.queryByRole('link', { name: 'Prise de commande — détail' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Traitement des retours' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tous' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Participant' }), 'Client')
    expect(screen.getByRole('link', { name: 'Prise de commande — détail' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Traitement des retours' })).not.toBeInTheDocument()
  })

  it('importer un fichier : vérifications, aperçu, clé et nom proposés (critère 1)', async () => {
    const calls = setup()
    await screen.findByRole('link', { name: 'Prise de commande — détail' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '⇪ Importer un fichier BPMN' }))
    const dialog = screen.getByRole('dialog', { name: 'Importer un fichier BPMN' })
    const file = new File(['<bpmn:definitions/>'], 'traitement-retours.bpmn', { type: 'application/xml' })
    await user.upload(within(dialog).getByLabelText('Choisir un fichier .bpmn'), file)
    expect(await within(dialog).findByText('XML BPMN 2.0 valide')).toBeInTheDocument()
    expect(within(dialog).getByText('Dessin présent (section bpmndi)')).toBeInTheDocument()
    expect(within(dialog).getByText('1 participant, 2 couloirs, 9 éléments, 8 flux')).toBeInTheDocument()
    expect(within(dialog).getByText('Aucun élément rattaché à un objet de la cartographie')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Clé')).toHaveValue('PRC-TRAITEMENT-DES-RETOURS')
    expect(within(dialog).getByText('Rangé dans cartographie/processus/prc-traitement-des-retours.bpmn')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Nom')).toHaveValue('Traitement des retours')
    // Les processus métier seulement, pas les rôles.
    const detail = within(dialog).getByLabelText('Détail du processus métier')
    expect(within(detail).queryByRole('option', { name: /Vendeur/ })).not.toBeInTheDocument()
    expect(dialog.querySelectorAll('.carto-bpmn-shape').length).toBeGreaterThan(0)
    await user.click(within(dialog).getByRole('button', { name: 'Importer et ouvrir' }))
    await screen.findByRole('heading', { name: 'Traitement des retours' })
    const imported = calls.find((c) => c.path === `${BASE}/processus/importer`)
    expect(JSON.parse(String(imported?.body))).toEqual({
      key: 'PRC-TRAITEMENT-DES-RETOURS',
      name: 'Traitement des retours',
      detail_of: null,
      content: '<bpmn:definitions/>',
    })
  })

  it('un fichier sans dessin ne s’importe pas (critère 2)', async () => {
    setup({ check: { ...CHECK, has_diagram: false } })
    await screen.findByRole('link', { name: 'Prise de commande — détail' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '⇪ Importer un fichier BPMN' }))
    const dialog = screen.getByRole('dialog', { name: 'Importer un fichier BPMN' })
    await user.upload(within(dialog).getByLabelText('Choisir un fichier .bpmn'), new File(['x'], 'x.bpmn'))
    expect(await within(dialog).findByText('Aucun dessin (section bpmndi) : le fichier sera refusé')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Importer et ouvrir' })).toBeDisabled()
  })

  it('créer un processus rattaché (critère 3)', async () => {
    const calls = setup()
    await screen.findByRole('link', { name: 'Prise de commande — détail' })
    const user = userEvent.setup()
    await user.click(screen.getAllByRole('button', { name: '+ Nouveau processus' })[0])
    const dialog = screen.getByRole('dialog', { name: 'Nouveau processus' })
    await user.type(within(dialog).getByLabelText('Clé'), 'prc-vente-detail')
    await user.type(within(dialog).getByLabelText('Nom'), 'Vente — détail')
    await user.selectOptions(within(dialog).getByLabelText('Détail du processus métier'), 'processus-metier/PRC-PRISE-COMMANDE')
    await user.click(within(dialog).getByRole('button', { name: 'Créer et ouvrir' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === `${BASE}/processus`)).toBe(true))
    const post = calls.find((c) => c.method === 'POST' && c.path === `${BASE}/processus`)
    expect(JSON.parse(String(post?.body))).toEqual({
      key: 'PRC-VENTE-DETAIL',
      name: 'Vente — détail',
      detail_of: 'processus-metier/PRC-PRISE-COMMANDE',
    })
  })

  it('rattacher un processus importé', async () => {
    const calls = setup()
    await screen.findByRole('link', { name: 'Traitement des retours' })
    const user = userEvent.setup()
    await user.click(within(card('Traitement des retours')).getByRole('button', { name: 'Rattacher…' }))
    const dialog = screen.getByRole('dialog', { name: 'Rattacher « Traitement des retours »' })
    await user.selectOptions(within(dialog).getByLabelText('Détail du processus métier'), 'processus-metier/PRC-RETOURS-METIER')
    await user.click(within(dialog).getByRole('button', { name: 'Rattacher' }))
    await waitFor(() => expect(calls.some((c) => c.path === `${BASE}/processus/PRC-RETOURS/rattacher`)).toBe(true))
    const attach = calls.find((c) => c.path === `${BASE}/processus/PRC-RETOURS/rattacher`)
    expect(JSON.parse(String(attach?.body))).toEqual({ detail_of: 'processus-metier/PRC-RETOURS-METIER' })
  })

  it('sans le droit de modifier : vignettes visibles, ni création ni import (critère 5)', async () => {
    setup({ capabilities: ['cartographie:read'] })
    await screen.findByRole('link', { name: 'Prise de commande — détail' })
    expect(screen.queryByRole('button', { name: '⇪ Importer un fichier BPMN' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ Nouveau processus' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rattacher…' })).not.toBeInTheDocument()
  })
})
