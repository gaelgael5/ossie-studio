import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCurrentWorkspace } from '../apiClient'
import { GovernanceInvalidValuesPage } from './GovernanceInvalidValuesPage'

function setup(values: unknown[]) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      calls.push(path)
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ values })) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <GovernanceInvalidValuesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

beforeEach(() => setCurrentWorkspace('s1', 'b1'))
afterEach(() => vi.unstubAllGlobals())

describe('GovernanceInvalidValuesPage', () => {
  it('liste les valeurs invalides avec un lien vers l’asset (ticket 5e8680d7)', async () => {
    const calls = setup([
      {
        source: 'api',
        path: 'commande.schema.yaml',
        title: 'Commande',
        field: '',
        scheme: 'classification',
        value: 'Secret',
        reason: 'unknown_level',
      },
    ])
    const link = await screen.findByRole('link', { name: 'Commande' })
    expect(link).toHaveAttribute('href', '/curation?source=api&path=commande.schema.yaml&tab=governance')
    expect(screen.getByText('Secret')).toBeInTheDocument()
    expect(screen.getByText('niveau inconnu')).toBeInTheDocument()
    expect(screen.getByText('(asset)')).toBeInTheDocument()
    expect(calls).toContain('/api/spaces/s1/branches/b1/governance/classifications/invalid-values')
  })

  it('état vide explicite', async () => {
    setup([])
    expect(await screen.findByText('Aucune valeur invalide.')).toBeInTheDocument()
  })
})
