import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { SpacePlugins } from './SpacePlugins'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const calls: string[] = []
function mock(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      calls.push(url)
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
}

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SpacePlugins spaceId="s1" fallbackBranchId="b0" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('SpacePlugins (ticket a30b4bd8)', () => {
  it('plugins utilisés, leurs sources cliquables, et les non utilisés', async () => {
    mock({
      sources: 2,
      used: [
        { id: 'csharp', label: 'C#', version: '2.0', origin: 'native', sources: [{ slug: 'colis-21', label: 'Colis 21' }] },
      ],
      unused: [{ id: 'python', label: 'Python', version: '1.0', origin: 'native' }],
    })
    renderIt()
    expect(await screen.findByText('C#')).toBeInTheDocument()
    expect(screen.getByText('natif')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Colis 21' })).toHaveAttribute('href', '/sources?tab=raw&source=colis-21')
    expect(screen.getByText(/Installés, non utilisés/).textContent).toContain('Python')
    // La branche du bandeau (b1), pas le repli (b0).
    expect(calls.some((u) => u.includes('/branches/b1/plugins'))).toBe(true)
  })

  it('sans source : message explicite', async () => {
    mock({ sources: 0, used: [], unused: [{ id: 'csharp', label: 'C#', version: '2.0', origin: 'native' }] })
    renderIt()
    expect(await screen.findByText(/Aucune source déclarée/)).toBeInTheDocument()
  })
})
