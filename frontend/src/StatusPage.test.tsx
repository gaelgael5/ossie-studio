import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import { StatusPage } from './StatusPage'
import type { HealthReport } from './api'

function renderWithQuery() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <StatusPage />
    </QueryClientProvider>,
  )
}

function mockHealth(report: HealthReport) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ json: () => Promise.resolve(report) }),
  )
}

afterEach(() => vi.unstubAllGlobals())

test('affiche « opérationnel » quand tout va', async () => {
  mockHealth({
    ok: true,
    database: { reachable: true, schema_version: 3, expected_version: 3, up_to_date: true, detail: null },
    volumes: [{ name: 'work', path: '/work', writable: true, detail: null }],
  })
  renderWithQuery()
  await waitFor(() => expect(screen.getByText('Service opérationnel')).toBeInTheDocument())
})

test('affiche « dégradé » quand un volume est non inscriptible', async () => {
  mockHealth({
    ok: false,
    database: { reachable: true, schema_version: 3, expected_version: 3, up_to_date: true, detail: null },
    volumes: [{ name: 'work', path: '/work', writable: false, detail: 'répertoire absent' }],
  })
  renderWithQuery()
  await waitFor(() => expect(screen.getByText('Service dégradé')).toBeInTheDocument())
})
