import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SourceDetailPage } from './SourceDetailPage'

vi.mock('../workspace', () => ({
  useWorkspace: () => ({ spaceId: 's1', branchId: 'b1', spaces: [], branches: [] }),
}))

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{`${loc.pathname}${loc.search}`}</output>
}

function renderAt(entry: string, body = '{"sources":[]}') {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(body) })),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/sources/:slug" element={<SourceDetailPage />} />
          <Route path="/sources" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('SourceDetailPage — anciens onglets Curation et Revue', () => {
  it('?tab=curation&object=… mène à l’objet dans l’explorateur raw', async () => {
    renderAt('/sources/app?tab=curation&object=order.json')
    expect(await screen.findByTestId('where')).toHaveTextContent(
      '/sources?tab=raw&source=app&sel=order.json',
    )
  })

  it('?tab=revue mène à l’explorateur de la source (la vue « Revue » n’existe plus)', async () => {
    renderAt('/sources/app?tab=revue')
    expect(await screen.findByTestId('where')).toHaveTextContent('/sources?tab=raw&source=app')
    expect(screen.getByTestId('where')).not.toHaveTextContent('view=review')
  })
})

describe('SourceDetailPage — bouton Collecter', () => {
  it('une source Snowflake se collecte : le bouton est actif (917102e5)', async () => {
    const body = JSON.stringify({
      sources: [
        { slug: 'sf', label: 'SF', type: 'snowflake', ok: true, connection: { slug: 'c' }, parameters: { database: 'DB' } },
      ],
    })
    renderAt('/sources/sf', body)
    const button = await screen.findByRole('button', { name: 'Collecter' })
    await vi.waitFor(() => expect(button).toBeEnabled())
  })
})
