import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConsentPage, consentNavigation } from './ConsentPage'

const REQUEST = {
  client: { id: 'c1', name: 'Claude', redirect_uri: 'https://claude.ai/api/mcp/auth_callback' },
  agent_profiles: [
    { id: 'p-lecture', name: 'Lecture', description: '', granted: ['list_spaces'], withheld: ['read_schema'] },
    { id: 'p-reperage', name: 'Repérage', description: '', granted: ['list_spaces'], withheld: [] },
  ],
  current_agent_profile_id: null,
}

function mockApi(request: unknown = REQUEST, status = 200) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      if (method === 'GET') {
        const body = status === 200 ? request : { error: 'oauth_request_not_found' }
        return Promise.resolve({ ok: status === 200, status, text: () => Promise.resolve(JSON.stringify(body)) })
      }
      const redirect = path.endsWith('/deny')
        ? 'https://claude.ai/cb?error=access_denied&state=s'
        : 'https://claude.ai/cb?code=abc&state=s'
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ redirect_url: redirect })) })
    }),
  )
  return calls
}

function renderPage(search = '?request=r1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/oauth/consent${search}`]}>
        <ConsentPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ConsentPage', () => {
  it('montre le client, ce qui sera accordé et ce qui ne le sera pas', async () => {
    mockApi()
    renderPage()
    expect(await screen.findByText(/« Claude » demande à agir pour vous/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Lecture' })).toBeChecked()
    expect(screen.getByText('list_spaces')).toBeInTheDocument()
    expect(screen.getByText(/Non accordé.*read_schema/)).toBeInTheDocument()
  })

  it('autorise avec le profil choisi puis renvoie vers le client', async () => {
    const calls = mockApi()
    const go = vi.spyOn(consentNavigation, 'go').mockImplementation(() => undefined)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('radio', { name: 'Repérage' }))
    await user.click(screen.getByRole('button', { name: 'Autoriser' }))
    await waitFor(() => expect(go).toHaveBeenCalledWith('https://claude.ai/cb?code=abc&state=s'))
    const post = calls.find((c) => c.method === 'POST')
    expect(post?.path).toBe('/api/oauth/requests/r1/approve')
    expect(JSON.parse(post?.body ?? '{}')).toEqual({ agent_profile_id: 'p-reperage' })
  })

  it('refuse sans émettre de code', async () => {
    const calls = mockApi()
    const go = vi.spyOn(consentNavigation, 'go').mockImplementation(() => undefined)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Refuser' }))
    await waitFor(() => expect(go).toHaveBeenCalledWith('https://claude.ai/cb?error=access_denied&state=s'))
    expect(calls.some((c) => c.path.endsWith('/approve'))).toBe(false)
  })

  it('sans profil utilisable : rien à accorder, seulement Refuser', async () => {
    mockApi({ ...REQUEST, agent_profiles: [] })
    renderPage()
    expect(await screen.findByText(/il n’y a rien à accorder/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Autoriser' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refuser' })).toBeInTheDocument()
  })

  it('demande expirée ou inconnue : le dit', async () => {
    mockApi(undefined, 404)
    renderPage()
    expect(await screen.findByText(/n’existe plus/)).toBeInTheDocument()
  })
})
