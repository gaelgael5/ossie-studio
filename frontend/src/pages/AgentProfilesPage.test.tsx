import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentProfilesPage } from './AgentProfilesPage'

const CATALOG = [
  { id: 'list_spaces', domain: 'spaces', access: 'read', capability: 'spaces:read', person_only: false },
  { id: 'get_my_draft', domain: 'drafts', access: 'read', capability: 'spaces:read', person_only: true },
  { id: 'get_reference_graph', domain: 'sources', access: 'read', capability: 'sources:read', person_only: false },
  { id: 'retain_asset', domain: 'curation', access: 'write', capability: 'curation:write', person_only: true },
]
const LECTURE = {
  id: 'p-lecture',
  slug: 'lecture',
  name: 'Lecture',
  description: '',
  primitives: [],
  all_read: true,
  is_builtin: true,
  effective: ['get_my_draft', 'get_reference_graph', 'list_spaces'],
  used_by: { keys: ['Clé CI'], connections: [] },
}
const REPERAGE = {
  ...LECTURE,
  id: 'p-reperage',
  slug: 'reperage',
  name: 'Repérage',
  primitives: ['list_spaces'],
  all_read: false,
  is_builtin: false,
  effective: ['list_spaces'],
  used_by: { keys: [], connections: [] },
}

function mockApi(deleteError?: unknown) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      if (method === 'DELETE' && deleteError) {
        return Promise.resolve({ ok: false, status: 409, text: () => Promise.resolve(JSON.stringify(deleteError)) })
      }
      const body = method === 'GET' ? { profiles: [LECTURE, REPERAGE], catalog: CATALOG } : REPERAGE
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentProfilesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('AgentProfilesPage', () => {
  it('le profil livré s’ouvre en lecture seule ; il se duplique, ne se supprime pas', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    const row = (await screen.findByText('Lecture')).closest('tr') as HTMLElement
    expect(within(row).getByText('livré')).toBeInTheDocument()
    expect(within(row).getByText('1 clé(s), 0 connexion(s)')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Supprimer' })).not.toBeInTheDocument()

    await user.click(row)
    const panel = screen.getByRole('region', { name: 'Profil livré « Lecture »' })
    expect(within(panel).getByText(/il ne se modifie pas/)).toBeInTheDocument()
    expect(within(panel).getByText('Lister les espaces et leurs branches')).toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: 'Enregistrer' })).not.toBeInTheDocument()
    await user.click(within(panel).getByRole('button', { name: 'Dupliquer' }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.path === '/api/agent-profiles/p-lecture/duplicate')).toBe(true),
    )
  })

  it('les boutons d’action n’ouvrent pas la ligne', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    const row = (await screen.findByText('Repérage')).closest('tr') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Supprimer' }))
    expect(screen.queryByRole('region', { name: 'Modifier le profil agent' })).not.toBeInTheDocument()
  })

  it('crée un profil avec les primitives cochées, en montrant ce que chacune suppose', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '+ Nouveau profil agent' }))
    const form = screen.getByRole('region', { name: 'Nouveau profil agent' })
    expect(within(form).getByText(/suppose : Sources — Consulter/)).toBeInTheDocument()
    expect(within(form).getAllByText(/agent connecté pour une personne seulement/).length).toBeGreaterThan(0)
    await user.type(within(form).getByLabelText('Nom'), 'Sources seules')
    await user.click(within(form).getByRole('checkbox', { name: /Voisinage d.un objet raw/ }))
    await user.click(within(form).getByRole('button', { name: 'Créer le profil' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const post = calls.find((c) => c.method === 'POST')
    expect(JSON.parse(post?.body ?? '{}')).toEqual({
      name: 'Sources seules',
      slug: 'sources-seules',
      description: '',
      all_read: false,
      primitives: ['get_reference_graph'],
    })
  })

  it('« toutes les lectures » coche et fige chaque lecture, sans les envoyer une à une', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '+ Nouveau profil agent' }))
    const form = screen.getByRole('region', { name: 'Nouveau profil agent' })
    await user.type(within(form).getByLabelText('Nom'), 'Tout lire')
    await user.click(within(form).getByRole('checkbox', { name: /Lister les espaces/ }))
    await user.click(within(form).getByRole('checkbox', { name: /Toutes les primitives de lecture/ }))
    const spaces = within(form).getByRole('checkbox', { name: /Lister les espaces/ })
    expect(spaces).toBeChecked()
    expect(spaces).toBeDisabled()
    await user.click(within(form).getByRole('button', { name: 'Créer le profil' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const post = JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')
    expect(post.all_read).toBe(true)
    expect(post.primitives).toEqual([])
  })

  it('cliquer n’importe où sur la ligne rouvre la configuration du profil', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    const row = (await screen.findByText('Repérage')).closest('tr') as HTMLElement
    await user.click(within(row).getByText('0 clé(s), 0 connexion(s)'))
    const form = screen.getByRole('region', { name: 'Modifier le profil agent' })
    expect(row).toHaveClass('selected')
    expect(within(form).getByLabelText('Nom')).toHaveValue('Repérage')
    expect(within(form).getByRole('checkbox', { name: /Lister les espaces/ })).toBeChecked()
    await user.click(within(form).getByRole('checkbox', { name: /Voisinage d.un objet raw/ }))
    await user.click(within(form).getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')
    expect(put?.path).toBe('/api/agent-profiles/p-reperage')
    expect(JSON.parse(put?.body ?? '{}').primitives.sort()).toEqual(['get_reference_graph', 'list_spaces'])
  })

  it('suppression confirmée ; en service, le refus nomme les clés et connexions', async () => {
    const calls = mockApi({
      error: 'agent_profile_in_use',
      error_args: { keys: 'Clé de Gael', connections: '—' },
    })
    const user = userEvent.setup()
    renderPage()
    const row = (await screen.findByText('Repérage')).closest('tr') as HTMLElement
    await user.click(within(row).getByRole('button', { name: 'Supprimer' }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    await user.click(within(row).getByRole('button', { name: 'Confirmer la suppression' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
    expect(await screen.findByText(/en service — clés : Clé de Gael/)).toBeInTheDocument()
  })

  it('une primitive d’écriture est montrée comme telle et ne vient jamais avec « toutes les lectures »', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '+ Nouveau profil agent' }))
    const form = screen.getByRole('region', { name: 'Nouveau profil agent' })
    const retain = within(form).getByRole('checkbox', { name: /Retenir un objet comme asset/ })
    expect(retain.closest('label')).toHaveTextContent('écriture')
    expect(retain.closest('label')).toHaveTextContent('suppose : Curation — Modifier')
    await user.click(within(form).getByRole('checkbox', { name: /Toutes les primitives de lecture/ }))
    expect(retain).not.toBeChecked()
    expect(retain).toBeEnabled()
  })
})
