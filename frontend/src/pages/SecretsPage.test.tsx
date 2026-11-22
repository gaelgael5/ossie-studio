import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SecretsPage } from './SecretsPage'

const LIST = {
  secrets: [{ slug: 'gh-ci', label: 'Jeton CI', category: 'github', updated_at: '2026-01-01T00:00:00Z', used_by: 0 }],
  categories: { github: 'GitHub' },
  category_inputs: { github: 'masked' },
}

function mockApi() {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push(`${method} ${path}`)
      const body = method === 'GET' && path.startsWith('/api/secrets') ? LIST : {}
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
        <SecretsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('SecretsPage — suppression', () => {
  it('demande confirmation ; Annuler ne supprime rien', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Supprimer' }))
    expect(calls.some((c) => c.startsWith('DELETE'))).toBe(false)
    expect(screen.getByRole('alert')).toHaveTextContent('Jeton CI')
    await user.click(screen.getByRole('button', { name: 'Annuler' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(calls.some((c) => c.startsWith('DELETE'))).toBe(false)
  })

  it('supprime après confirmation', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Supprimer' }))
    await user.click(screen.getByRole('button', { name: 'Confirmer la suppression' }))
    await waitFor(() => expect(calls).toContain('DELETE /api/secrets/gh-ci'))
  })
})
