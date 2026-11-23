import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DraftChangesPage } from './DraftChangesPage'

const many = Array.from({ length: 60 }, (_, i) => ({
  file: `obj${String(i).padStart(2, '0')}.json`,
  title: `Obj${i}`,
  state: 'created',
  folder: 'src',
}))

const CHANGES = {
  count: 5,
  mode: 'origin',
  sources: [
    {
      slug: 'colis-21',
      label: 'colis 21',
      raw: [
        { file: 'order.json', title: 'Order', state: 'modified', folder: 'src/Domain' },
        { file: 'old.json', title: 'old.json', state: 'deleted', folder: null },
      ],
      assets: [{ file: 'order.json', title: 'Order', state: 'created', folder: 'src/Domain' }],
      orphans: [{ file: 'ghost.json', title: 'ghost.json', state: 'orphan', folder: null }],
      other: [{ path: 'sources/colis-21/manifest.yaml', state: 'modified' }],
    },
    { slug: 'crm', label: 'CRM', raw: many, assets: [], orphans: [], other: [] },
  ],
  other: [{ path: 'governance/tags/pii.yaml', state: 'created' }],
}

function renderPage(body: unknown = CHANGES) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) }),
    ),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/spaces/s1/branches/b1/changes']}>
        <Routes>
          <Route path="/spaces/:spaceId/branches/:branchId/changes" element={<DraftChangesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('DraftChangesPage', () => {
  it('range le brouillon par source et par nature, avec liens vers l’explorateur et la Curation', async () => {
    renderPage()
    const colis = within(await screen.findByRole('region', { name: 'colis 21' }))
    expect(colis.getByText('Objets raw (2)')).toBeInTheDocument()
    const [rawLink, assetLink] = colis.getAllByRole('link', { name: 'Order' })
    expect(rawLink).toHaveAttribute(
      'href',
      '/sources?tab=raw&source=colis-21&mode=origin&path=src%2FDomain&sel=order.json',
    )
    expect(assetLink).toHaveAttribute('href', '/curation?source=colis-21&path=order.json')
    // Un objet supprimé n'a plus de lien.
    expect(colis.queryByRole('link', { name: 'old.json' })).not.toBeInTheDocument()
    expect(colis.getByText('supprimé')).toBeInTheDocument()
    expect(colis.getByText('Assets (1)')).toBeInTheDocument()
    expect(colis.getByText('Assets orphelins (1)')).toBeInTheDocument()
    expect(colis.getByRole('link', { name: 'ghost.json' })).toHaveAttribute(
      'href',
      '/curation?source=colis-21&path=ghost.json',
    )
    expect(colis.getByText('sources/colis-21/manifest.yaml')).toBeInTheDocument()
    expect(screen.getByText('Hors des sources')).toBeInTheDocument()
    expect(screen.getByText('governance/tags/pii.yaml')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Publier' })).toHaveAttribute('href', '/spaces/s1/branches/b1/publish')
  })

  it('une longue liste se replie, « Afficher tout » la déplie', async () => {
    const user = userEvent.setup()
    renderPage()
    const crm = within(await screen.findByRole('region', { name: 'CRM' }))
    expect(crm.getAllByRole('row')).toHaveLength(51) // en-tête + 50
    await user.click(crm.getByRole('button', { name: 'Afficher tout (60)' }))
    expect(crm.getAllByRole('row')).toHaveLength(61)
  })

  it('brouillon vide : le dit, sans bouton Publier', async () => {
    renderPage({ count: 0, mode: 'origin', sources: [], other: [] })
    expect(await screen.findByText('Aucune modification en cours sur cette branche.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Publier' })).not.toBeInTheDocument()
  })
})
