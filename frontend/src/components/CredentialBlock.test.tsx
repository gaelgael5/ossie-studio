import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CredentialBlock } from './CredentialBlock'

const BASE = {
  id: 'c1',
  connection_id: 'k1',
  forge: 'github',
  url: 'https://github.com',
  forge_login: null,
  error_code: null,
  checked_at: null,
}

function mockFetch(responses: Record<string, unknown>) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      const body = responses[`${method} ${path}`] ?? {}
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderBlock() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CredentialBlock spaceId="s1" />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('CredentialBlock', () => {
  it('saisit puis teste le jeton, sans jamais réafficher la valeur', async () => {
    const calls = mockFetch({
      'GET /api/me/credentials?space_id=s1': { ...BASE, status: 'a_saisir' },
      'PUT /api/me/credentials/c1': { ...BASE, status: 'jamais_teste' },
      'POST /api/me/credentials/c1/test': { ...BASE, status: 'valide', forge_login: 'octocat' },
    })
    const user = userEvent.setup()
    renderBlock()
    await waitFor(() => expect(screen.getByText('à saisir')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Tester le secret' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Saisir le secret' }))
    await user.type(screen.getByLabelText('Jeton personnel'), 'ghp_secret')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(screen.getByText('jamais testé')).toBeInTheDocument())
    expect(screen.queryByDisplayValue('ghp_secret')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tester le secret' }))
    await waitFor(() => expect(screen.getByText('valide')).toBeInTheDocument())
    expect(screen.getByText('jeton du compte octocat')).toBeInTheDocument()
    expect(JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')).toEqual({ space_id: 's1' })
  })

  it('montre le refus faute de droit d’écriture', async () => {
    mockFetch({
      'GET /api/me/credentials?space_id=s1': {
        ...BASE,
        status: 'refuse',
        forge_login: 'lecteur',
        error_code: 'credential_no_write',
      },
    })
    renderBlock()
    await waitFor(() => expect(screen.getByText('refusé')).toBeInTheDocument())
    expect(screen.getByText('Ce jeton n’a pas le droit d’écrire sur le dépôt de l’espace.')).toBeInTheDocument()
  })
})
