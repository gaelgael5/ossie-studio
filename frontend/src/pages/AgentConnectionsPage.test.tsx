import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentConnectionsPage } from './AgentConnectionsPage'

const CONNECTION = {
  id: 'c1',
  client: { id: 'claude-1', name: 'Claude' },
  agent_profile: { id: 'p-lecture', name: 'Lecture' },
  user: 'gael@example.org',
  state: 'active',
  created_at: '2026-11-22T10:00:00Z',
  updated_at: '2026-11-22T10:00:00Z',
  last_used_at: null,
  revoked_at: null,
}
const PROFILES = [
  { id: 'p-lecture', name: 'Lecture', description: '', granted: ['list_spaces'], withheld: [] },
  { id: 'p-reperage', name: 'Repérage', description: '', granted: ['list_spaces'], withheld: [] },
]

function mockApi() {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = CONNECTION
      if (method === 'GET' && path === '/api/agent-connections') body = { connections: [CONNECTION], agent_profiles: PROFILES }
      if (method === 'GET' && path === '/api/agent-connections/all')
        body = { connections: [{ ...CONNECTION, id: 'c2', user: 'ana@example.org' }] }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderPage(props = { canSeeAll: false, canRevokeAll: false }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentConnectionsPage {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('AgentConnectionsPage', () => {
  it('change le profil agent d’une connexion', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('Claude')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Profil agent'), 'p-reperage')
    await user.click(screen.getByRole('button', { name: 'Appliquer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')
    expect(put?.path).toBe('/api/agent-connections/c1/profile')
    expect(JSON.parse(put?.body ?? '{}')).toEqual({ agent_profile_id: 'p-reperage' })
  })

  it('révoque après confirmation', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Révoquer' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Claude')
    await user.click(screen.getByRole('button', { name: 'Confirmer la révocation' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/api/agent-connections/c1/revoke')).toBe(true))
  })

  it('montre toutes les connexions à qui en a la capacité', async () => {
    mockApi()
    renderPage({ canSeeAll: true, canRevokeAll: false })
    const all = await screen.findByRole('region', { name: 'Toutes les connexions' })
    expect(await within(all).findByText('ana@example.org')).toBeInTheDocument()
    expect(within(all).queryByRole('button', { name: 'Révoquer' })).not.toBeInTheDocument()
  })

  it('sans la capacité, pas de vue d’ensemble', async () => {
    const calls = mockApi()
    renderPage()
    await screen.findByText('Claude')
    expect(screen.queryByRole('region', { name: 'Toutes les connexions' })).not.toBeInTheDocument()
    expect(calls.some((c) => c.path.endsWith('/all'))).toBe(false)
  })
})
