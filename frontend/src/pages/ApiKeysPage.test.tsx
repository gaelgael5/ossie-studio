import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiKeysPage } from './ApiKeysPage'

const SECRET = 'osk_abcdefgh0123456789abcdefgh0123456789abcde'

const KEY = {
  id: 'k1',
  label: 'Claude Code de Gael',
  prefix: 'osk_abcdefgh',
  agent_profile: { id: 'p-lecture', name: 'Lecture' },
  state: 'active',
  created_at: '2026-11-22T10:00:00Z',
  created_by: 'gael@example.org',
  revoked_at: null,
  revoked_by: null,
  last_used_at: null,
}

const OPTIONS = {
  endpoint: 'https://ossie.example.org/mcp',
  endpoint_configured: true,
  agent_profiles: [
    { id: 'p-admin', name: 'Tout', description: '', primitives: ['list_spaces'], grantable: false, beyond: ['list_spaces'] },
    { id: 'p-lecture', name: 'Lecture', description: 'Lectures', primitives: ['list_spaces'], grantable: true, beyond: [] },
    { id: 'p-vide', name: 'Vide', description: '', primitives: [], grantable: true, beyond: [] },
  ],
}

function mockApi(keys: unknown[] = [KEY]) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (method === 'GET' && path === '/api/api-keys') body = { keys }
      if (method === 'GET' && path === '/api/api-keys/options') body = OPTIONS
      if (method === 'POST' && path === '/api/api-keys') body = { key: { ...KEY, label: 'Nouvelle' }, secret: SECRET }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ApiKeysPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('ApiKeysPage', () => {
  it('liste les clés par leur préfixe et montre l’adresse de la surface MCP', async () => {
    mockApi()
    renderPage()
    expect(await screen.findByText('Claude Code de Gael')).toBeInTheDocument()
    expect(screen.getByText('osk_abcdefgh…')).toBeInTheDocument()
    expect(screen.getAllByText('https://ossie.example.org/mcp').length).toBeGreaterThan(0)
  })

  it('émet une clé avec un profil et ne la montre qu’une fois', async () => {
    const calls = mockApi([])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '+ Nouvelle clé' }))
    const form = screen.getByRole('region', { name: 'Émettre une clé' })

    // Le profil au-delà des droits est visible mais grisé ; le premier possible est choisi.
    const select = within(form).getByLabelText('Profil agent') as HTMLSelectElement
    expect(within(select).getByRole('option', { name: 'Tout — au-delà de vos droits' })).toBeDisabled()
    expect(select.value).toBe('p-lecture')

    await user.type(within(form).getByLabelText('Libellé'), 'Nouvelle')
    await user.click(within(form).getByRole('button', { name: 'Émettre' }))

    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const post = calls.find((c) => c.method === 'POST')
    expect(JSON.parse(post?.body ?? '{}')).toEqual({ label: 'Nouvelle', agent_profile_id: 'p-lecture' })

    const secretPanel = await screen.findByRole('status', { name: 'Votre nouvelle clé « Nouvelle »' })
    expect(within(secretPanel).getByText(SECRET)).toBeInTheDocument()
    expect(secretPanel).toHaveTextContent(
      `claude mcp add --transport http ossie-studio https://ossie.example.org/mcp --header "Authorization: Bearer ${SECRET}"`,
    )
    await user.click(within(secretPanel).getByRole('button', { name: 'C’est noté, masquer la clé' }))
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument()
  })

  it('révoque seulement après confirmation', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Révoquer' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Claude Code de Gael')
    expect(calls.some((c) => c.path.endsWith('/revoke'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Confirmer la révocation' }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.path === '/api/api-keys/k1/revoke')).toBe(true),
    )
  })

  it('change le profil d’une clé active', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    const apply = await screen.findByRole('button', { name: 'Appliquer' })
    expect(apply).toBeDisabled()
    const select = screen.getByLabelText('Profil agent') as HTMLSelectElement
    expect(within(select).getByRole('option', { name: 'Tout — au-delà de vos droits' })).toBeDisabled()
    await user.selectOptions(select, 'p-vide')
    await user.click(apply)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')
    expect(put?.path).toBe('/api/api-keys/k1/profile')
    expect(JSON.parse(put?.body ?? '{}')).toEqual({ agent_profile_id: 'p-vide' })
  })
})
