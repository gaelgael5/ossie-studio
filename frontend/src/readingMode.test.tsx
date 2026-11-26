import { act, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from './i18n'
import { api } from './apiClient'
import { resetReadingMode, setReadingMode } from './readingMode'
import { DraftBar } from './components/DraftBar'
import { SourcesPage } from './pages/SourcesPage'

vi.mock('./workspace', () => ({
  useWorkspace: () => ({ spaceId: 's1', branchId: 'b1', spaces: [{ id: 's1', name: 'TEST' }], branches: [] }),
}))
vi.mock('./pages/RawExplorer', () => ({ RawExplorer: () => <p>explorateur</p> }))

beforeAll(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(() => {
  vi.restoreAllMocks()
  act(() => resetReadingMode())
})

function wrap(node: React.ReactNode, url = '/sources?tab=raw') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/sources" element={node} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('mode lecture de l’explorateur (4e287004)', () => {
  it('replie l’en-tête et les onglets de la page Sources, et les rétablit', async () => {
    vi.spyOn(api, 'listSources').mockResolvedValue({ sources: [] } as never)
    vi.spyOn(api, 'listSchemes').mockResolvedValue({ schemes: [] } as never)
    wrap(<SourcesPage />)
    expect(await screen.findByText('explorateur')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Explorateur raw' })).toBeInTheDocument()
    act(() => setReadingMode(true))
    expect(screen.queryByRole('tab', { name: 'Explorateur raw' })).not.toBeInTheDocument()
    expect(screen.getByText('explorateur')).toBeInTheDocument()
    expect(sessionStorage.getItem('ossie.readingMode')).toBe('1')
    act(() => setReadingMode(false))
    expect(screen.getByRole('tab', { name: 'Explorateur raw' })).toBeInTheDocument()
  })

  it('la barre du brouillon, cachée par défaut, réapparaît si le publié a changé', async () => {
    const draft = { count: 2, changed_upstream_count: 0, objects: [] }
    const spy = vi.spyOn(api, 'myDraft').mockResolvedValue(draft as never)
    const { unmount } = wrap(<DraftBar spaceId="s1" branchId="b1" />)
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByRole('region', { name: /brouillon/i })).not.toBeInTheDocument()
    unmount()
    spy.mockResolvedValue({ ...draft, changed_upstream_count: 1 } as never)
    wrap(<DraftBar spaceId="s1" branchId="b1" />)
    expect(await screen.findByRole('region', { name: /brouillon/i })).toBeInTheDocument()
  })
})
