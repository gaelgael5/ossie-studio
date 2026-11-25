import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from '../i18n'
import { api } from '../apiClient'
import { SourcesPage } from './SourcesPage'

vi.mock('../workspace', () => ({
  useWorkspace: () => ({ spaceId: 's1', branchId: 'b1', spaces: [{ id: 's1', name: 'TEST' }], branches: [{ id: 'b1', name: 'main' }] }),
}))

const SF = {
  slug: 'dv', label: 'Datavault', type: 'snowflake', ok: true,
  connection: { slug: 'sf', label: 'SF', kind: 'db', engine: 'snowflake' },
  parameters: { database: 'DB_DVT_PRD' }, triage_count: 0, classification: [],
}

beforeAll(async () => {
  await i18n.changeLanguage('fr')
})
afterEach(() => vi.restoreAllMocks())

describe('SourcesPage — modifier une source base (0d85e746)', () => {
  it('« Enregistrer et collecter » enregistre, collecte et ouvre la source', async () => {
    vi.spyOn(api, 'listSources').mockResolvedValue({ sources: [SF] } as never)
    vi.spyOn(api, 'listSchemes').mockResolvedValue({ schemes: [] } as never)
    vi.spyOn(api, 'listDbConnections').mockResolvedValue({
      connections: [{ id: 'c1', slug: 'sf', label: 'SF', engine: 'snowflake' }],
    } as never)
    vi.spyOn(api, 'listDbSchemas').mockResolvedValue({ schemas: [] })
    vi.spyOn(api, 'getCurationRules').mockResolvedValue({ rules: [] } as never)
    const update = vi.spyOn(api, 'updateSource').mockResolvedValue(SF as never)
    const collect = vi.spyOn(api, 'collectSource').mockResolvedValue({} as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/sources']}>
          <Routes>
            <Route path="/sources" element={<SourcesPage />} />
            <Route path="/sources/:slug" element={<output>page de la source</output>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await screen.findByText('Datavault')
    // La ligne d'une source base a maintenant son lien « Collecter ».
    expect(screen.getByRole('link', { name: 'Collecter' })).toHaveAttribute('href', '/sources/dv')
    await userEvent.click(screen.getByRole('button', { name: 'Modifier' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: 'Déclarer' })).not.toBeInTheDocument()
    const both = within(dialog).getByRole('button', { name: 'Enregistrer et collecter' })
    await waitFor(() => expect(both).toBeEnabled())
    await userEvent.click(both)
    await waitFor(() => expect(collect).toHaveBeenCalledWith('s1', 'b1', 'dv', false))
    expect(update).toHaveBeenCalled()
    expect(await screen.findByText('page de la source')).toBeInTheDocument()
  })
})
