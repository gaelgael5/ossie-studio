import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBranchHead } from './useBranchHead'

afterEach(() => vi.unstubAllGlobals())

describe('useBranchHead', () => {
  it('relit les données de la branche quand la tête publiée change, pas sinon', async () => {
    const shas = ['aaa', 'aaa', 'bbb']
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        const sha = shas.length > 1 ? shas.shift() : shas[0]
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ sha })) })
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    renderHook(() => useBranchHead('s1', 'b1'), { wrapper })
    await waitFor(() => expect(client.getQueryData(['branch-head', 's1', 'b1'])).toEqual({ sha: 'aaa' }))
    await client.refetchQueries({ queryKey: ['branch-head', 's1', 'b1'] })
    expect(invalidate).not.toHaveBeenCalled() // même tête
    await client.refetchQueries({ queryKey: ['branch-head', 's1', 'b1'] })
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(1))
  })
})
