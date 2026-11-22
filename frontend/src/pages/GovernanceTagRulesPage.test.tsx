import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCurrentWorkspace } from '../apiClient'
import { GovernanceTagRulesPage } from './GovernanceTagRulesPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1'

const CTRL = {
  id: 'ctrl',
  path: 'governance/tag-rules/ctrl.yaml',
  errors: [],
  builtin: false,
  rule: {
    id: 'ctrl',
    name: 'Contrôleurs',
    enabled: true,
    files: ['*.cs'],
    match: 'any',
    when: { inherits: ['ControllerBase'], annotation: ['ApiController'] },
    tags: ['technique/service'],
    inherit_tags: true,
  },
}
const BROKEN = { id: 'broken', path: 'governance/tag-rules/broken.yaml', errors: ['tag_rule_files_invalid'], rule: null, builtin: false }
const PROVIDED = {
  id: 'ossie-data-contact',
  path: '',
  errors: [],
  builtin: true,
  rule: { ...CTRL.rule, id: 'ossie-data-contact', name: 'Données — contact', when: { field: ['*email*'] }, tags: ['donnees/personnelles/contact'], inherit_tags: false },
}

const HITS = {
  objects: 3,
  inherited: 2,
  examples: [{ slug: 'app', file: 'orders.json', title: 'OrdersController', folder: 'src/Api', inherited: false }],
}

function mockApi(capabilities: string[] = ['governance:read', 'governance:write', 'sources:read']) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      let status = 200
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path.startsWith(`${BASE}/governance/tag-rules`) && method === 'GET') body = { rules: [PROVIDED, CTRL, BROKEN] }
      else if (path.startsWith(`${BASE}/governance/tags`)) body = { tags: [{ id: 'technique/service', owner: '', description: '', links: {}, properties: {} }] }
      else if (path.startsWith(`${BASE}/raw-explorer/tag-rules/usage`)) body = { rules: { ctrl: HITS } }
      else if (path.startsWith(`${BASE}/raw-explorer/tag-rules/preview`)) body = HITS
      else if (method === 'PUT' && path.endsWith('/state')) body = { ...PROVIDED, created_tags: [] }
      else if (method === 'PUT' && path.includes('/governance/tag-rules/')) {
        const sent = JSON.parse(init?.body as string)
        if (sent.files.length === 0) {
          status = 400
          body = { error: 'tag_rule_invalid', error_args: { fields: ['tag_rule_files_invalid'] } }
        } else body = { ...CTRL, created_tags: ['technique/dto'] }
      }
      return Promise.resolve({
        ok: status < 400,
        status,
        text: () => Promise.resolve(JSON.stringify(body)),
        json: () => Promise.resolve(body),
      })
    }),
  )
  return calls
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GovernanceTagRulesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => setCurrentWorkspace('s1', 'b1'))
afterEach(() => vi.unstubAllGlobals())

describe('GovernanceTagRulesPage', () => {
  it('liste les règles avec état et décompte, détaille conditions et exemples', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('Contrôleurs')).toBeInTheDocument()
    expect(screen.getByText('invalide')).toBeInTheDocument()
    expect(await screen.findByText('3 objet(s) tagué(s)')).toBeInTheDocument()
    await user.click(screen.getByText('Contrôleurs'))
    expect(screen.getByText('hérite de ControllerBase, ou porte ApiController')).toBeInTheDocument()
    expect(screen.getByText('3 objet(s), dont 2 par héritage.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'OrdersController' })).toHaveAttribute(
      'href',
      '/sources?tab=raw&source=app&sel=orders.json&path=src%2FApi',
    )
    // Une règle invalide montre ses erreurs dans les mots du journal de validation.
    await user.click(screen.getByText('broken'))
    expect(screen.getByText(/indiquez au moins un motif de fichiers/)).toBeInTheDocument()
  })

  it('désactiver réenregistre la règle avec enabled à false', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Contrôleurs'))
    await user.click(screen.getByRole('button', { name: 'Désactiver' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe(`${BASE}/governance/tag-rules/ctrl`)
    expect(JSON.parse(put.body!).enabled).toBe(false)
  })

  it('crée une règle : identifiant dérivé, aperçu en direct, tags nouveaux signalés', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '+ Nouvelle règle' }))
    await user.type(screen.getByLabelText('Nom'), 'Objets DTO')
    expect(screen.getByLabelText('Identifiant (nom du fichier)')).toHaveValue('objets-dto')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Type de condition' }), 'name')
    await user.type(screen.getByRole('textbox', { name: 'Valeurs (séparées par des virgules)' }), '*Dto')
    await user.type(screen.getByLabelText('Tags posés'), 'technique/dto')
    expect(screen.getByText(/ajouté\(s\) automatiquement à l’enregistrement : technique\/dto/)).toBeInTheDocument()
    const preview = await screen.findByText('3 objet(s), dont 2 par héritage.', {}, { timeout: 3000 })
    expect(preview).toBeInTheDocument()
    const sentPreview = calls.filter((c) => c.path.includes('/preview')).pop()!
    expect(JSON.parse(sentPreview.body!)).toMatchObject({
      files: ['*.cs'],
      when: { name: ['*Dto'] },
      tags: ['technique/dto'],
    })
    await user.click(screen.getByRole('button', { name: 'Enregistrer dans le brouillon' }))
    expect(
      await screen.findByText('Règle enregistrée dans le brouillon. Tags ajoutés au vocabulaire : technique/dto.'),
    ).toBeInTheDocument()
    expect(calls.find((c) => c.method === 'PUT')!.path).toBe(`${BASE}/governance/tag-rules/objets-dto`)
  })

  it('une saisie refusée affiche ce qu’il faut corriger', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '+ Nouvelle règle' }))
    await user.type(screen.getByLabelText('Nom'), 'X')
    await user.clear(screen.getByLabelText('Fichiers ciblés'))
    await user.click(screen.getByRole('button', { name: 'Enregistrer dans le brouillon' }))
    expect(await screen.findByText(/indiquez au moins un motif de fichiers/)).toBeInTheDocument()
  })

  it('sans governance:write : lecture seule', async () => {
    mockApi(['governance:read'])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Contrôleurs'))
    expect(screen.queryByRole('button', { name: '+ Nouvelle règle' })).not.toBeInTheDocument()
    const panel = screen.getByText('governance/tag-rules/ctrl.yaml').closest('.panel') as HTMLElement
    expect(within(panel).queryByRole('button', { name: 'Désactiver' })).not.toBeInTheDocument()
  })

  it('règle fournie par Ossie : ni modifiable ni supprimable, désactivation par son état', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('Données — contact'))
    expect(screen.getByText('contient le champ *email*')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifier la règle' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Supprimer' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Désactiver' }))
    await waitFor(() => expect(calls.some((c) => c.path.endsWith('/state'))).toBe(true))
    const put = calls.find((c) => c.path.endsWith('/state'))!
    expect(put.path).toBe(`${BASE}/governance/tag-rules/ossie-data-contact/state`)
    expect(JSON.parse(put.body!)).toEqual({ enabled: false })
  })
})
