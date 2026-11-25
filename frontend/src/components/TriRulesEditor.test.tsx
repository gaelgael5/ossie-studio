import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TriRulesEditor, useTriRules } from './TriRulesEditor'

const URL_RULES = '/api/spaces/s1/branches/b1/sources/app/curation/rules'

function mockApi() {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      const body =
        method === 'GET'
          ? {
              rules: [{ target: 'object-name', kind: 'glob', pattern: '*Dto', label: '' }],
              targets: ['object-name', 'field-name'],
              kinds: ['glob', 'regex'],
            }
          : { ok: true }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function Harness({ slug }: { slug?: string }) {
  const tri = useTriRules('s1', 'b1', slug)
  return (
    <>
      <TriRulesEditor state={tri} />
      <button type="button" onClick={() => void tri.persist()}>
        enregistrer
      </button>
      <output data-testid="dirty">{String(tri.dirty)}</output>
    </>
  )
}

function renderHarness(slug?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Harness slug={slug} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('TriRulesEditor', () => {
  it('charge les règles de la source, les modifie et ne les enregistre que si elles ont changé', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderHarness('app')
    expect(await screen.findByDisplayValue('*Dto')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'enregistrer' }))
    expect(calls.some((c) => c.method === 'PUT')).toBe(false) // rien de changé
    await user.click(screen.getByRole('button', { name: 'Ajouter une règle' }))
    const patterns = screen.getAllByRole('textbox', { name: 'Motif' })
    await user.type(patterns[1], 'STAGING*')
    expect(screen.getByTestId('dirty')).toHaveTextContent('true')
    await user.click(screen.getByRole('button', { name: 'enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe(URL_RULES)
    expect(JSON.parse(put.body!).rules.map((r: { pattern: string }) => r.pattern)).toEqual([
      '*Dto',
      'STAGING*',
    ])
  })
})
