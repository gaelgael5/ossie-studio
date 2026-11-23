import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SourceItem } from '../apiClient'
import { SourceExclusions } from './SourceExclusions'

const SOURCE = {
  slug: 'colis-21',
  ok: true,
  label: 'colis 21',
  type: 'git',
  connection: { slug: 'forge', label: 'Forge' },
  parameters: { repository: 'o/colis21', branch: 'main', parsers: ['csharp'], exclusions: ['tests', 'tools'] },
} as unknown as SourceItem

function setup(onRecollect = vi.fn()) {
  const calls: { method: string; url: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ method: init?.method ?? 'GET', url, body: init?.body as string | undefined })
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{}') })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SourceExclusions spaceId="s1" branchId="b1" source={SOURCE} onRecollect={onRecollect} />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('SourceExclusions', () => {
  it('liste les exclusions et les défauts ; retirer en garde les autres, puis propose la collecte', async () => {
    const onRecollect = vi.fn()
    const calls = setup(onRecollect)
    const user = userEvent.setup()
    expect(screen.getByText('tests')).toBeInTheDocument()
    expect(screen.getByText('tools')).toBeInTheDocument()
    // Défauts affichés, sans bouton pour les retirer.
    expect(screen.getByText('node_modules')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Retirer l’exclusion' })).toHaveLength(2)
    await user.click(screen.getAllByRole('button', { name: 'Retirer l’exclusion' })[0])
    expect(
      await screen.findByText('Exclusion de « tests » retirée : ses objets reviendront à la prochaine collecte.'),
    ).toBeInTheDocument()
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.url).toBe('/api/spaces/s1/branches/b1/sources/colis-21')
    expect(JSON.parse(put.body!).parameters.exclusions).toEqual(['tools'])
    await user.click(screen.getByRole('button', { name: 'Relancer la collecte' }))
    expect(onRecollect).toHaveBeenCalled()
  })
})
