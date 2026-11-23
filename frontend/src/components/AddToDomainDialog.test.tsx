import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AddToDomainDialog } from './AddToDomainDialog'

const BASE = '/api/spaces/s1/branches/b1/domains'
const PLAN = {
  added: [
    { name: 'Order', source: 'raw:app/order.schema.yaml', consultable: false, derived: true, asset: null, description: '', fields: [], primary_key: [] },
    { name: 'Customer', source: 'raw:app/customer.schema.yaml', consultable: false, derived: true, asset: null, description: '', fields: [], primary_key: [] },
  ],
  already: [],
  links: [
    { id: 'ref:Order.customer->Customer', kind: 'reference', from_dataset: 'Order', to_dataset: 'Customer', field: 'customer', ref_kind: 'field', cardinality: 'n:1', to_column: '' },
  ],
  ignored: [{ source: 'app', path: 'status.schema.yaml', title: 'Status' }],
}

function setup() {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === BASE) body = { domains: [{ name: 'ventes', description: '', consultable: 0, virtual: 0, assets: [], warnings: [] }] }
      else if (path.endsWith('/preview')) body = PLAN
      else if (path.endsWith('/assets')) body = { ...PLAN, commit: 'draft:4' }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <AddToDomainDialog
          spaceId="s1"
          branchId="b1"
          assets={[{ source: 'app', path: 'order.schema.yaml' }]}
          withReferences
          onClose={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('AddToDomainDialog (ticket 36d35a88)', () => {
  it('nouveau domaine : aperçu sans écriture, lien décoché non envoyé, création au regroupement', async () => {
    const calls = setup()
    const user = userEvent.setup()
    const preview = screen.getByRole('button', { name: 'Voir ce qui sera ajouté' })
    expect(preview).toBeDisabled() // nom vide
    await user.type(screen.getByLabelText('Nom'), 'ventes')
    expect(preview).toBeDisabled() // déjà pris
    await user.clear(screen.getByLabelText('Nom'))
    await user.type(screen.getByLabelText('Nom'), 'expedition')
    await user.click(preview)
    expect(await screen.findByText('Order')).toBeInTheDocument()
    expect(screen.getByText(/Status/)).toBeInTheDocument() // ignoré, signalé
    expect(calls.some((c) => c.path.endsWith('/assets'))).toBe(false)
    const link = screen.getByRole('checkbox', { name: /Order\.customer → Customer/ })
    expect(link).toBeChecked()
    await user.click(link)
    await user.click(screen.getByRole('button', { name: 'Regrouper' }))
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/assets'))).toBe(true))
    const sent = calls.find((c) => c.path.endsWith('/assets'))!
    expect(sent.path).toBe(`${BASE}/expedition/assets`)
    expect(JSON.parse(sent.body!)).toEqual({
      assets: [{ source: 'app', path: 'order.schema.yaml' }],
      with_references: true,
      create: true,
      description: '',
      links: [],
    })
    expect(await screen.findByText('Regroupé dans le domaine « expedition ».')).toBeInTheDocument()
  })

  it('domaine existant : pas de création demandée', async () => {
    const calls = setup()
    const user = userEvent.setup()
    await screen.findByRole('option', { name: 'ventes' })
    await user.selectOptions(screen.getByLabelText('Domaine'), 'ventes')
    await user.click(screen.getByRole('button', { name: 'Voir ce qui sera ajouté' }))
    await screen.findByText('Order')
    expect(JSON.parse(calls.find((c) => c.path.endsWith('/preview'))!.body!).create).toBe(false)
  })
})
