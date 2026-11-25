import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCurrentWorkspace } from '../apiClient'
import { GovernanceClassificationRulesPage } from './GovernanceClassificationRulesPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1'

const GDPR = {
  id: 'rgpd',
  name: 'GDPR',
  description: '',
  badge_prefix: '',
  multi_value: false,
  is_primary: false,
  display_order: 0,
  levels: [
    { id: 'np', name: 'Non personnelle', label: '', short_name: 'NP', rank: 1, description: '', criteria: '', color: '', icon: '', position: 1 },
    { id: 'dp', name: 'Donnée personnelle', label: '', short_name: 'DP', rank: 2, description: '', criteria: '', color: '', icon: '', position: 0 },
  ],
}
const EMAILS = {
  id: 'emails',
  path: 'governance/classification-rules/emails.yaml',
  errors: [],
  rule: {
    id: 'emails',
    name: 'Emails',
    enabled: true,
    files: [],
    match: 'all',
    when: { field: ['*email*'], type: ['string'] },
    scheme: 'rgpd',
    values: ['Donnée personnelle'],
  },
}
const GONE = { ...EMAILS, id: 'gone', path: 'governance/classification-rules/gone.yaml', errors: ['classification_rule_scheme_unknown'], rule: { ...EMAILS.rule, id: 'gone', name: 'Ancienne', scheme: 'absent' } }
const PROVIDED = {
  ...EMAILS,
  id: 'ossie-rgpd-telephone',
  path: '',
  builtin: true,
  rule: { ...EMAILS.rule, id: 'ossie-rgpd-telephone', name: 'RGPD — téléphones', when: { field: ['*phone*'], type: ['string'] } },
}
const FIELDS = { objects: [{ source: 'app', file: 'customer.schema.yaml', title: 'Customer', origin: 'src/Customer.cs', folder: 'src', fields: ['Email', 'customer_email'] }] }

function mockApi() {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities: ['governance:read', 'governance:write', 'sources:read'] }
      else if (path.startsWith(`${BASE}/governance/classification-rules`) && method === 'GET') body = { rules: [PROVIDED, EMAILS, GONE] }
      else if (path.startsWith(`${BASE}/governance/classifications`)) body = { schemes: [GDPR] }
      else if (path.endsWith('/classification-rules/usage')) body = { rules: { emails: 2 } }
      else if (path.endsWith('/classification-rules/emails/fields')) body = FIELDS
      else if (path.endsWith('/classification-rules/preview')) body = { fields: 2, object_count: 1, objects: FIELDS.objects }
      else if (method === 'PUT' && path.endsWith('/state')) body = { ...PROVIDED, rule: { ...PROVIDED.rule, enabled: false } }
      else if (method === 'PUT') body = EMAILS
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <GovernanceClassificationRulesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => setCurrentWorkspace('s1', 'b1'))
afterEach(() => vi.unstubAllGlobals())

describe('GovernanceClassificationRulesPage (ticket cfc33d51)', () => {
  it('onglet après Classifications, liste, détail et champs classés', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    expect(tabs.indexOf('Règles de classification')).toBe(tabs.indexOf('Classifications') + 1)
    expect(await screen.findByText('Emails')).toBeInTheDocument()
    expect(await screen.findByText('2 champ(s) classé(s)')).toBeInTheDocument()
    await user.click(screen.getByText('Emails'))
    expect(screen.getByText('nom du champ *email* et type string')).toBeInTheDocument()
    expect(screen.getByText('tous les fichiers')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Customer' })).toHaveAttribute(
      'href',
      '/sources?tab=raw&source=app&sel=customer.schema.yaml&path=src',
    )
    expect(screen.getByText('Email, customer_email')).toBeInTheDocument()
    // Schéma disparu : la règle est signalée invalide avec la raison.
    await user.click(screen.getByText('Ancienne'))
    expect(screen.getByText('Le schéma de classification n’existe pas (ou plus).')).toBeInTheDocument()
  })

  it('règle fournie par Ossie : badge, ni modifier ni supprimer, désactivable (ticket c7ce55aa)', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('RGPD — téléphones'))
    expect(screen.getAllByText('Ossie').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Supprimer' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifier la règle' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Désactiver' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/ossie-rgpd-telephone/state'))).toBe(true))
    const put = calls.find((c) => c.path.endsWith('/state'))!
    expect(JSON.parse(put.body!)).toEqual({ enabled: false })
  })

  it('crée une règle : conditions, schéma, valeur, aperçu puis enregistrement', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '+ Nouvelle règle' }))
    await user.type(screen.getByLabelText('Nom'), 'Téléphones')
    expect(screen.getByLabelText('Identifiant (nom du fichier)')).toHaveValue('telephones')
    await user.type(screen.getByLabelText('Valeurs (séparées par des virgules)'), '*phone*')
    await user.click(screen.getByRole('button', { name: '+ Condition' }))
    const kinds = screen.getAllByLabelText('Type de condition')
    expect(kinds[1]).toHaveValue('type')
    await user.type(screen.getAllByLabelText('Valeurs (séparées par des virgules)')[1], 'string')
    await user.selectOptions(screen.getByLabelText('Schéma de classification'), 'rgpd')
    await user.selectOptions(screen.getByLabelText('Valeur'), 'Donnée personnelle')
    expect(await screen.findByText('2 champ(s) dans 1 objet(s).', {}, { timeout: 3000 })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer dans le brouillon' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe(`${BASE}/governance/classification-rules/telephones`)
    expect(JSON.parse(put.body!)).toMatchObject({
      name: 'Téléphones',
      match: 'all',
      files: [],
      when: { field: ['*phone*'], type: ['string'] },
      scheme: 'rgpd',
      values: ['Donnée personnelle'],
    })
  })
})
