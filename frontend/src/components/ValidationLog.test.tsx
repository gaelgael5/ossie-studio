import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ValidationLog } from './ValidationLog'

afterEach(() => vi.unstubAllGlobals())

describe('ValidationLog', () => {
  it('distingue erreur bloquante, erreur préexistante et avertissement acceptable', async () => {
    const posts: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((_path: string, init?: RequestInit) => {
        if (init?.method === 'POST') posts.push(init.body as string)
        const body =
          init?.method === 'POST'
            ? { accepted: true }
            : {
                blocking: 1,
                entries: [
                  { level: 'error', path: 'sources/s/raw/O.json', rule: 'broken_ref', message_code: 'ref_broken', args: { target: 'sources/s/raw/X.json' }, preexisting: false, accepted: false, blocking: true },
                  { level: 'error', path: 'sources/v/manifest.yaml', rule: 'parseable', message_code: 'file_unreadable', args: {}, preexisting: true, accepted: false, blocking: false },
                  { level: 'warning', path: 'sources/s/assets/A.json', rule: 'orphan_asset', message_code: 'asset_orphan', args: {}, preexisting: false, accepted: false, blocking: false },
                ],
              }
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const user = userEvent.setup()
    render(
      <QueryClientProvider client={client}>
        <ValidationLog spaceId="s1" branchId="b1" />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByText('Journal de validation — erreurs bloquantes : 1')).toBeInTheDocument())
    expect(screen.getByText('référence cassée vers sources/s/raw/X.json')).toBeInTheDocument()
    expect(screen.getByText('préexistant')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Accepter' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(JSON.parse(posts[0] ?? '{}')).toEqual({ rule: 'orphan_asset', path: 'sources/s/assets/A.json', message_code: 'asset_orphan' })
  })
})
