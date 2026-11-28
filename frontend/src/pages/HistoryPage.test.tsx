import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HistoryPage } from './HistoryPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const EVENTS = [
  {
    at: '2026-11-23T10:00:00+00:00',
    actor_label: 'Camille D. <camille@example.org>',
    actor_id: 'u1',
    action: 'deleted',
    object_type: 'secret',
    object_id: 'jeton',
    object_label: 'Jeton forge',
    detail: {},
    origin: 'db',
    ref: '3',
  },
  {
    at: '2026-11-22T09:00:00+00:00',
    actor_label: 'Camille',
    actor_id: null,
    action: 'created',
    object_type: 'certification',
    object_id: 'or',
    object_label: 'or',
    detail: { commit: 'abcdef123456', message: 'gouvernance' },
    origin: 'git',
    ref: 'abcdef1234567890',
  },
]

function setup(pages: unknown[]) {
  const calls: string[] = []
  let i = 0
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      calls.push(path)
      const body = pages[Math.min(i++, pages.length - 1)]
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('HistoryPage (ticket cf4434da)', () => {
  it('liste les deux origines, filtre et pagine', async () => {
    const calls = setup([
      { events: EVENTS, next: '2026-11-22T09:00:00+00:00', types: ['secret', 'certification'] },
      { events: [], next: null, types: ['secret', 'certification'] },
    ])
    expect(await screen.findByText('Jeton forge')).toBeInTheDocument()
    expect(screen.getByText('Supprimé')).toBeInTheDocument()
    expect(screen.getByText('Git abcdef1')).toBeInTheDocument()
    expect(calls[0]).toContain('space_id=s1')
    expect(calls[0]).toContain('branch_id=b1')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Plus ancien' }))
    await waitFor(() => expect(calls.some((c) => c.includes('before='))).toBe(true))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Type d’objet' }), 'secret')
    await waitFor(() => expect(calls.some((c) => c.includes('type=secret'))).toBe(true))
  })

  it('un import qui a gardé des éléments car utilisés mène à leur page (ticket 6d36073b)', async () => {
    const run = {
      at: '2026-11-28T10:00:00+00:00',
      actor_label: 'Camille',
      actor_id: 'u1',
      action: 'updated',
      object_type: 'carto_import',
      object_id: 'b1/document/fichier',
      object_label: 'document › fichier',
      detail: { outcome: 'imported', deletion: 'all', kept_in_use: 1 },
      origin: 'db',
      ref: '41',
    }
    setup([{ events: [run, { ...run, ref: '40', detail: { outcome: 'imported', kept_in_use: 0 } }], next: null, types: [] }])
    const links = await screen.findAllByRole('link', { name: 'Éléments gardés car utilisés' })
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/cartographie/types/document/imports/fichier/executions/41'])
  })

  it('état vide explicite', async () => {
    setup([{ events: [], next: null, types: [] }])
    expect(await screen.findByText('Aucun événement pour ces critères.')).toBeInTheDocument()
  })
})
