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

function renderAt(entry: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{"sources":[]}') }),
    ),
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

  it('?tab=revue mène à la vue « Revue » de la source', async () => {
    renderAt('/sources/app?tab=revue')
    expect(await screen.findByTestId('where')).toHaveTextContent(
      '/sources?tab=raw&source=app&view=review',
    )
  })
})
