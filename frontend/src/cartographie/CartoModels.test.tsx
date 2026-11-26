import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CartographiePage } from '../pages/CartographiePage'
import { CartoModelPage } from './CartoModelPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const DATES = { created_at: '2026-11-20T10:00:00+00:00', created_by: 'alex.r', updated_at: '2026-11-25T10:00:00+00:00', updated_by: 'camille.d' }
const CATALOG = {
  archimate: '3.2',
  layers: [{ key: 'application', label: 'Application' }],
  base: [],
  types: [],
  viewpoints: [
    { id: 'layered', label: 'En couches' },
    { id: 'application-cooperation', label: 'Coopération applicative' },
  ],
}

function model(slug: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    slug,
    id: `MOD-${slug.toUpperCase()}`,
    name,
    description: '',
    path: `cartographie/modeles/${slug}.modele.yaml`,
    graphic: `cartographie/modeles/${slug}.graphique.yaml`,
    rules: 'cartographie/regles/standard.rules.yaml',
    rules_name: 'standard',
    views: [{ id: 'VUE-1', name: 'Vue', viewpoint: '' }],
    elements: 0,
    relationships: 0,
    keys: [],
    unused_documents: 0,
    errors: 0,
    preview: { boxes: [], links: [] },
    dates: DATES,
    state: null,
    ...extra,
  }
}

const MODELS = [
  model('boutique-applicatif', 'Boutique — vue applicative', {
    description: 'Ce que la boutique expose.',
    views: [
      { id: 'VUE-1', name: 'Coopération', viewpoint: '' },
      { id: 'VUE-2', name: 'Détail', viewpoint: '' },
    ],
    elements: 2,
    relationships: 1,
    keys: ['APP-BOUTIQUE', 'OBJ-COMMANDE', 'REL-ECRIT'],
    preview: {
      boxes: [
        { x: 40, y: 60, l: 200, h: 80, label: 'Boutique en ligne', kind: 'element' },
        { x: 340, y: 60, l: 120, h: 55, label: 'Commande', kind: 'element' },
      ],
      links: [{ x1: 140, y1: 100, x2: 400, y2: 87 }],
    },
  }),
  model('reclamations', 'Réclamations', {
    keys: ['SRV-CLIENT'],
    elements: 1,
    unused_documents: 1,
    errors: 1,
    state: 'created',
    dates: null,
  }),
]
const OBJECTS = [
  { key: 'APP-BOUTIQUE', name: 'Boutique en ligne' },
  { key: 'SRV-CLIENT', name: 'Service client' },
  { key: 'OBJ-FACTURE', name: 'Facture' },
].map((o) => ({ ...o, type: 't', type_title: 'T', archimate: '', excerpt: '', properties: [], relations: 0, is_relation: false, source: null, target: null, path: 'p', dates: null, state: null }))

function setup(options: { capabilities?: string[]; at?: string } = {}) {
  const calls: { method: string; path: string; body?: string }[] = []
  const capabilities = options.capabilities ?? ['cartographie:read', 'cartographie:write']
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path === `${BASE}/catalogue`) body = CATALOG
      else if (path === `${BASE}/types`) body = { readonly: false, types: [] }
      else if (path === `${BASE}/objets`) body = { readonly: false, objects: OBJECTS }
      else if (path === `${BASE}/modeles` && method === 'GET') body = { readonly: false, models: MODELS, rules: ['metier', 'standard'] }
      else if (path === `${BASE}/processus`) body = { readonly: false, processes: [] }
      else if (path === `${BASE}/modeles` && method === 'POST') {
        const req = JSON.parse(String(init?.body)) as { slug: string; label: string }
        body = { ...model(req.slug, req.label), state: 'created', dates: null, revision: 'draft:1' }
      } else if (path.startsWith(`${BASE}/modeles/`) && method === 'DELETE') body = { deleted: 'x', revision: 'draft:2' }
      else if (path.startsWith(`${BASE}/modeles/`)) body = { ...MODELS[0], problems: [] }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[options.at ?? '/cartographie/modeles']}>
        <Routes>
          <Route path="/cartographie" element={<CartographiePage />} />
          <Route path="/cartographie/modeles" element={<CartographiePage />} />
          <Route path="/cartographie/modeles/:slug" element={<CartoModelPage />} />
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
  const cover = screen.getByRole('link', { name })
  return cover.closest('article') as HTMLElement
}

describe('Cartographie — onglet Modèles (ticket 5a05c7bb)', () => {
  it('un troisième onglet, des vignettes avec aperçu, vues, nombres et règles', async () => {
    setup()
    await screen.findByRole('link', { name: 'Boutique — vue applicative' })
    expect(screen.getByRole('tab', { name: /Modèles/ })).toHaveAttribute('aria-selected', 'true')
    const boutique = card('Boutique — vue applicative')
    expect(within(boutique).getByText('2 vues')).toBeInTheDocument()
    expect(within(boutique).getByText('MOD-BOUTIQUE-APPLICATIF')).toBeInTheDocument()
    expect(within(boutique).getByText('2 objets')).toBeInTheDocument()
    expect(within(boutique).getByText('1 relation')).toBeInTheDocument()
    expect(within(boutique).getByText('règles : standard')).toBeInTheDocument()
    expect(within(boutique).getByText('modifié le 25/11')).toBeInTheDocument()
    expect(boutique.querySelectorAll('.carto-preview-box')).toHaveLength(2)
    expect(boutique.querySelectorAll('.carto-preview-link')).toHaveLength(1)
    // Un modèle à nettoyer est signalé ; il est encore dans le seul brouillon.
    const reclamations = card('Réclamations')
    expect(within(reclamations).getByText('1 document chargé sans objet retenu')).toBeInTheDocument()
    expect(within(reclamations).getByText('nouveau')).toBeInTheDocument()
    expect(within(reclamations).getByRole('link', { name: 'Ouvrir' })).toHaveAttribute(
      'href',
      '/cartographie/modeles/reclamations',
    )
    expect(screen.getByText('2 modèles')).toBeInTheDocument()
  })

  it('filtre « contient l’objet » et recherche (critère 2)', async () => {
    setup()
    await screen.findByRole('link', { name: 'Boutique — vue applicative' })
    const user = userEvent.setup()
    const contains = screen.getByRole('combobox', { name: 'Contient l’objet' })
    // Seuls les objets retenus par un modèle sont proposés.
    expect(within(contains).queryByRole('option', { name: /Facture/ })).not.toBeInTheDocument()
    await user.selectOptions(contains, 'APP-BOUTIQUE')
    expect(screen.getByRole('link', { name: 'Boutique — vue applicative' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Réclamations' })).not.toBeInTheDocument()
    await user.selectOptions(contains, '')
    await user.type(screen.getByRole('searchbox', { name: 'Rechercher un modèle…' }), 'reclam')
    expect(screen.queryByRole('link', { name: 'Boutique — vue applicative' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Réclamations' })).toBeInTheDocument()
  })

  it('créer un modèle : fichiers affichés, « Créer et ouvrir » ouvre le modèle (critère 1)', async () => {
    const calls = setup()
    await screen.findByRole('link', { name: 'Boutique — vue applicative' })
    const user = userEvent.setup()
    await user.click(screen.getAllByRole('button', { name: '+ Nouveau modèle' })[0])
    const dialog = screen.getByRole('dialog', { name: 'Nouveau modèle' })
    await user.type(within(dialog).getByLabelText('Libellé'), 'Vente en magasin')
    expect(within(dialog).getByLabelText('Slug')).toHaveValue('vente-en-magasin')
    expect(within(dialog).getByText('cartographie/modeles/vente-en-magasin.modele.yaml')).toBeInTheDocument()
    expect(within(dialog).getByText('cartographie/modeles/vente-en-magasin.graphique.yaml')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Fichier de règles')).toHaveValue('standard')
    expect(within(dialog).getByLabelText('Première vue')).toHaveValue('Vue principale')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Point de vue' }), 'layered')
    await user.click(within(dialog).getByRole('button', { name: 'Créer et ouvrir' }))
    await screen.findByRole('heading', { name: 'Boutique — vue applicative' })
    const post = calls.find((c) => c.method === 'POST')
    expect(JSON.parse(String(post?.body))).toEqual({
      label: 'Vente en magasin',
      slug: 'vente-en-magasin',
      description: '',
      rules: 'standard',
      view: 'Vue principale',
      viewpoint: 'layered',
    })
    expect(calls.some((c) => c.path === `${BASE}/modeles/vente-en-magasin`)).toBe(true)
  })

  it('un slug déjà pris est refusé avant l’envoi', async () => {
    setup()
    await screen.findByRole('link', { name: 'Boutique — vue applicative' })
    const user = userEvent.setup()
    await user.click(screen.getAllByRole('button', { name: '+ Nouveau modèle' })[0])
    const dialog = screen.getByRole('dialog', { name: 'Nouveau modèle' })
    await user.type(within(dialog).getByLabelText('Libellé'), 'Réclamations')
    expect(within(dialog).getByText('Ce slug est déjà pris par un modèle.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Créer et ouvrir' })).toBeDisabled()
  })

  it('supprimer un modèle par « … », après confirmation (critère 3)', async () => {
    const calls = setup()
    await screen.findByRole('link', { name: 'Boutique — vue applicative' })
    const user = userEvent.setup()
    const boutique = card('Boutique — vue applicative')
    await user.click(within(boutique).getByRole('button', { name: 'Actions du modèle' }))
    await user.click(within(boutique).getByRole('menuitem', { name: 'Supprimer le modèle' }))
    await user.click(within(boutique).getByRole('button', { name: 'Confirmer la suppression' }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'DELETE' && c.path === `${BASE}/modeles/boutique-applicatif`)).toBe(true),
    )
  })

  it('sans le droit de modifier : vignettes visibles et ouvrables, ni création ni suppression (critère 5)', async () => {
    setup({ capabilities: ['cartographie:read'] })
    await screen.findByRole('link', { name: 'Boutique — vue applicative' })
    expect(screen.queryByRole('button', { name: '+ Nouveau modèle' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Actions du modèle' })).not.toBeInTheDocument()
    expect(within(card('Réclamations')).getByRole('link', { name: 'Ouvrir' })).toBeInTheDocument()
  })
})
