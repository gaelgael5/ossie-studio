import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DomainPage, DomainsPage } from './DomainsPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1/domains'
const DETAIL = {
  name: 'expedition',
  description: 'Colis',
  consultable: 0,
  virtual: 2,
  assets: [],
  warnings: [],
  datasets: [
    { name: 'Order', source: 'raw:app/order.schema.yaml', consultable: false, derived: true, asset: { source: 'app', path: 'order.schema.yaml' }, description: '', fields: [{ name: 'id' }], primary_key: [] },
    { name: 'Customer', source: 'raw:app/customer.schema.yaml', consultable: false, derived: true, asset: { source: 'app', path: 'customer.schema.yaml' }, description: '', fields: [], primary_key: [] },
  ],
  relationships: [],
  references: [{ name: 'Order_customer', from_dataset: 'Order', field: 'customer', to_dataset: 'Customer', kind: 'field', cardinality: 'n:1' }],
}

function setup(url: string) {
  const calls: { method: string; path: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities: ['curation:read', 'curation:write'] }
      else if (path === BASE && method === 'GET') body = { domains: [] }
      else if (path === BASE) body = { ...DETAIL, datasets: undefined }
      else if (path === `${BASE}/expedition`) body = DETAIL
      return Promise.resolve({ ok: true, status: method === 'DELETE' ? 204 : 200, text: () => Promise.resolve(method === 'DELETE' ? '' : JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/domaines" element={<DomainsPage />} />
          <Route path="/domaines/:name" element={<DomainPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('Domaines (tickets a7baaaa3, 36d35a88)', () => {
  it('liste vide puis création', async () => {
    const calls = setup('/domaines')
    expect(await screen.findByText(/Aucun domaine/)).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '+ Nouveau domaine' }))
    await user.type(screen.getByLabelText('Nom'), 'expedition')
    await user.click(screen.getByRole('button', { name: 'Créer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === BASE)).toBe(true))
  })

  it('écran du domaine : datasets virtuels, liens, retrait d’un dataset', async () => {
    const calls = setup('/domaines/expedition')
    expect(await screen.findByText('Datasets virtuels (2)')).toBeInTheDocument()
    expect(screen.getByText('Order.customer → Customer (référence, n:1)')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Voir l’asset' })[0]).toHaveAttribute(
      'href',
      '/curation?source=app&path=order.schema.yaml',
    )
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Retirer Customer du domaine' }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'DELETE' && c.path === `${BASE}/expedition/datasets/Customer`)).toBe(true),
    )
  })
})
