import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCurrentWorkspace } from '../apiClient'
import { FieldRuleClasses } from './FieldRuleClasses'

beforeEach(() => setCurrentWorkspace('s1', 'b1'))
afterEach(() => vi.unstubAllGlobals())

function renderWith(schemes: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(schemes)) }),
    ),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <FieldRuleClasses
        classes={[{ scheme: 'rgpd', values: ['Donnée personnelle'], rule: 'emails', rule_name: 'Emails' }]}
      />
    </QueryClientProvider>,
  )
}

describe('FieldRuleClasses (ticket cfc33d51)', () => {
  it('badge du niveau en pointillés, règle au survol', async () => {
    renderWith({
      schemes: [
        { id: 'rgpd', name: 'GDPR', badge_prefix: '', multi_value: false, levels: [{ id: 'dp', name: 'Donnée personnelle', short_name: 'DP', rank: 2, color: '' }] },
      ],
    })
    const badge = await screen.findByText('DP')
    expect(badge).toHaveClass('class-derived')
    expect(screen.getByTitle('par la règle « Emails »')).toBeInTheDocument()
  })

  it('sans accès aux schémas : la valeur en clair', async () => {
    renderWith({ error: 'forbidden' }, 403)
    expect(await screen.findByText('Donnée personnelle')).toBeInTheDocument()
  })
})
