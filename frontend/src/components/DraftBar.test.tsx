import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { DraftBar, DraftViewSelect } from './DraftBar'
import { withDraftView } from '../draftView'

function mockDraft(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })),
  )
}

function renderBar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DraftViewSelect spaceId="s1" branchId="b1" />
        <DraftBar spaceId="s1" branchId="b1" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const DRAFT = {
  count: 2,
  revision: 3,
  changed_upstream_count: 1,
  objects: [
    { path: 'sources/a/manifest.yaml', state: 'modified', changed_upstream: true },
    { path: 'sources/b/manifest.yaml', state: 'created', changed_upstream: false },
  ],
}

describe('DraftBar', () => {
  it('ne s’affiche pas sans brouillon', async () => {
    mockDraft({ count: 0, revision: 0, changed_upstream_count: 0, objects: [] })
    const { container } = renderBar()
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('signale les objets changés côté publié et liste le brouillon en vue « Brouillon »', async () => {
    mockDraft(DRAFT)
    const user = userEvent.setup()
    renderBar()
    await waitFor(() =>
      expect(screen.getByText('1 objet(s) ont changé côté publié depuis votre brouillon.')).toBeInTheDocument(),
    )
    expect(screen.getByRole('combobox', { name: 'Vue de la branche' })).toHaveValue('merged')
    expect(screen.queryByText('sources/a/manifest.yaml')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Vue de la branche' }), 'draft')
    expect(screen.getByText('sources/a/manifest.yaml')).toBeInTheDocument()
    expect(screen.getByText('a changé côté publié')).toBeInTheDocument()
  })

  it('ticket e7e377b0 : rien de changé côté publié, vue fusionnée → aucun bandeau, juste la liste « Vue »', async () => {
    mockDraft({ ...DRAFT, changed_upstream_count: 0 })
    renderBar()
    expect(await screen.findByRole('combobox', { name: 'Vue de la branche' })).toBeInTheDocument()
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Publier' })).not.toBeInTheDocument()
  })

  it('la vue « Publiée » ajoute view=published aux lectures de la branche, pas aux écritures', async () => {
    mockDraft(DRAFT)
    const user = userEvent.setup()
    renderBar()
    const select = await screen.findByRole('combobox', { name: 'Vue de la branche' })
    await user.selectOptions(select, 'published')
    expect(window.localStorage.getItem('ossie.view.b1')).toBe('published')
    expect(withDraftView('/api/spaces/s1/branches/b1/sources', 'GET')).toBe(
      '/api/spaces/s1/branches/b1/sources?view=published',
    )
    expect(withDraftView('/api/spaces/s1/branches/b1/sources', 'POST')).toBe('/api/spaces/s1/branches/b1/sources')
    expect(withDraftView('/api/spaces/s1/branches/b1/draft', 'GET')).toBe('/api/spaces/s1/branches/b1/draft')
    expect(withDraftView('/api/spaces/s1/branches/b2/sources', 'GET')).toBe('/api/spaces/s1/branches/b2/sources')
  })
})
