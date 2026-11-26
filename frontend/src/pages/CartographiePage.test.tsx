import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CartographiePage } from './CartographiePage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 12h20"/></svg>'

function archimate(name: string, label: string, layer: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    kind: layer === '' ? 'relation' : 'element',
    layer,
    label,
    definition: `Définition de ${label}.`,
    icon: ICON,
    attributes: [],
    relations: ['Composition', 'Association'],
    ...extra,
  }
}

const CATALOG = {
  archimate: '3.2',
  layers: [
    { key: 'business', label: 'Métier' },
    { key: 'application', label: 'Application' },
  ],
  base: [
    { name: 'cle', type: 'string', required: true },
    { name: 'nom', type: 'string', required: true },
    { name: 'documentation', type: 'string', required: false },
  ],
  types: [
    archimate('BusinessActor', 'Acteur métier', 'business'),
    archimate('BusinessInteraction', 'Interaction métier', 'business'),
    archimate('ApplicationComponent', 'Composant applicatif', 'application'),
    archimate('Composition', 'Composition', ''),
    archimate('Association', 'Association', ''),
    archimate('Access', 'Accès', '', {
      attributes: [
        {
          name: 'acces',
          label: 'Sens de l’accès',
          type: 'string',
          values: [
            { value: 'Read', label: 'lecture' },
            { value: 'Write', label: 'écriture' },
            { value: 'ReadWrite', label: 'lecture-écriture' },
          ],
          unset: 'non précisé',
        },
      ],
    }),
  ],
}

const TYPES = [
  { slug: 'application', title: 'Application', archimate: 'ApplicationComponent', path: 'p', properties: 4, objects: 11, state: null },
  { slug: 'client', title: 'Client', archimate: 'BusinessActor', path: 'p', properties: 2, objects: 12, state: null },
  { slug: 'service-interne', title: 'Service interne', archimate: 'BusinessActor', path: 'p', properties: 1, objects: 6, state: 'created' },
]

function setup(options: { capabilities?: string[]; types?: unknown[] } = {}) {
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
      else if (path === `${BASE}/types` && method === 'GET') body = { readonly: false, types: options.types ?? TYPES }
      else if (path === `${BASE}/objets`) body = { readonly: false, objects: [] }
      else if (path === `${BASE}/modeles`) body = { readonly: false, models: [], rules: [] }
      else if (path === `${BASE}/types/apercu`) {
        const req = JSON.parse(String(init?.body)) as { label: string; slug: string; archimate: string }
        body = {
          path: `cartographie/types/${req.slug}.schema.yaml`,
          yaml: `title: ${req.label}\nx-archimate: ${req.archimate}\ntype: object\n`,
          problems: [],
        }
      } else if (path === `${BASE}/types`) body = { type: { ...TYPES[0], state: 'created' }, revision: 'draft:1' }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/cartographie']}>
        <Routes>
          <Route path="/cartographie" element={<CartographiePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Cartographie — types en vignettes (ticket e7521e80)', () => {
  it('une vignette par type, avec son type ArchiMate et ses nombres (critère 5)', async () => {
    setup()
    const client = await screen.findByRole('link', { name: 'Client' })
    // Un clic sur la vignette ouvre la fenêtre d'édition du type (ticket 5c39d4df).
    expect(client).toHaveAttribute('href', '/cartographie/types/client')
    expect(within(client).getByText('Acteur métier')).toBeInTheDocument()
    expect(within(client).getByText('2 propriétés')).toBeInTheDocument()
    expect(within(client).getByText('12 objets')).toBeInTheDocument()
    const internal = screen.getByRole('link', { name: 'Service interne' })
    expect(within(internal).getByText('Acteur métier')).toBeInTheDocument()
    expect(within(internal).getByText('nouveau')).toBeInTheDocument()
  })

  it('filtre par couche, par nom et par type ArchiMate (critère 7)', async () => {
    setup()
    await screen.findByRole('link', { name: 'Client' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Application' }))
    expect(screen.getByRole('link', { name: 'Application' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Client' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Application' }))
    await user.type(screen.getByRole('searchbox', { name: 'Filtrer par nom…' }), 'interne')
    expect(screen.getAllByRole('link').map((a) => a.getAttribute('aria-label'))).toEqual(['Service interne'])
    await user.clear(screen.getByRole('searchbox', { name: 'Filtrer par nom…' }))
    await user.selectOptions(screen.getByRole('combobox'), 'BusinessActor')
    expect(screen.getAllByRole('link').map((a) => a.getAttribute('aria-label'))).toEqual(['Client', 'Service interne'])
  })

  it('choisir un type sans rien saisir : nom par défaut, puis ajout (critères 1 et 2)', async () => {
    const calls = setup({ types: [] })
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: '+ Ajouter' }))[0])
    const dialog = screen.getByRole('dialog', { name: 'Ajouter un type' })
    await user.click(within(dialog).getByRole('button', { name: 'Composant applicatif' }))
    expect(within(dialog).getByLabelText('Libellé')).toHaveValue('Composant applicatif')
    expect(within(dialog).getByLabelText('Slug')).toHaveValue('composant-applicatif')
    expect(await within(dialog).findByText(/cartographie\/types\/composant-applicatif\.schema\.yaml/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Ajouter' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === `${BASE}/types`)).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST' && c.path === `${BASE}/types`)!.body!)).toEqual({
      label: 'Composant applicatif',
      slug: 'composant-applicatif',
      archimate: 'ApplicationComponent',
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('un libellé saisi est conservé quand on change de type (critère 3)', async () => {
    setup({ types: [] })
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: '+ Ajouter' }))[0])
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Interaction métier' }))
    await user.clear(within(dialog).getByLabelText('Libellé'))
    await user.type(within(dialog).getByLabelText('Libellé'), 'Application')
    await user.click(within(dialog).getByRole('button', { name: 'Composant applicatif' }))
    expect(within(dialog).getByLabelText('Libellé')).toHaveValue('Application')
    expect(within(dialog).getByLabelText('Slug')).toHaveValue('application')
  })

  it('Accès montre la propriété acces en plus des propriétés de base (critère 4)', async () => {
    setup({ types: [] })
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: '+ Ajouter' }))[0])
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Accès' }))
    for (const name of ['cle', 'nom', 'documentation', 'acces']) {
      expect(within(dialog).getByText(name)).toBeInTheDocument()
    }
    expect(within(dialog).getByText('lecture, écriture, lecture-écriture')).toBeInTheDocument()
  })

  it('un slug déjà pris est refusé avant l’envoi (critère 6)', async () => {
    setup()
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: '+ Ajouter' }))[0])
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Acteur métier' }))
    await user.clear(within(dialog).getByLabelText('Libellé'))
    await user.type(within(dialog).getByLabelText('Libellé'), 'Client')
    expect(within(dialog).getByText('Ce slug est déjà pris par un type de l’espace.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Ajouter' })).toBeDisabled()
  })

  it('sans le droit de modifier : vignettes visibles, pas de « + Ajouter » (critère 8)', async () => {
    setup({ capabilities: ['cartographie:read'] })
    expect(await screen.findByRole('link', { name: 'Client' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ Ajouter' })).not.toBeInTheDocument()
  })
})
