import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ImpactsSection } from './ImpactsSection'

afterEach(() => vi.unstubAllGlobals())

describe('ImpactsSection', () => {
  it('liste les impacts et marque « vérifié »', async () => {
    let verified = false
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${path}`)
        if (init?.method === 'POST') verified = true
        const body =
          init?.method === 'POST'
            ? { path: 'a', verified: true }
            : {
                impacts: [{ path: 'sources/s/assets/U.json', reads: ['sources/s/raw/U.json'], rules: ['raw_asset'], verified }],
                pending: verified ? 0 : 1,
              }
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={client}>
        <ImpactsSection spaceId="s1" branchId="b1" />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByText('Impacts à traiter : 1')).toBeInTheDocument())
    expect(screen.getByText('lit sources/s/raw/U.json')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Vérifié, rien à corriger' }))
    await waitFor(() => expect(screen.getByText('vérifié')).toBeInTheDocument())
    expect(calls).toContain('POST /api/spaces/s1/branches/b1/draft/impacts/verify')
  })
})
