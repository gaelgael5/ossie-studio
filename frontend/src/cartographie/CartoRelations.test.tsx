import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CartographiePage } from '../pages/CartographiePage'
import { CartoObjectPage } from './CartoObjectPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))
vi.mock('../components/MarkdownEditor', () => ({ default: () => <div /> }))

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 12h20"/></svg>'
const el = (name: string, label: string, extra: Record<string, unknown> = {}) => ({ name, kind: 'element', layer: 'business', label, definition: `Définition de ${label}.`, icon: ICON, attributes: [], relations: [], verbs: { out: '', in: '' }, ...extra })
const rel = (name: string, label: string, out: string, inn: string, extra: Record<string, unknown> = {}) => el(name, label, { kind: 'relation', layer: '', verbs: { out, in: inn }, ...extra })
const ACCES_ATTR = { name: 'acces', label: 'Sens de l’accès', type: 'string', values: [{ value: 'Read', label: 'lecture' }, { value: 'Write', label: 'écriture' }, { value: 'ReadWrite', label: 'lecture-écriture' }], unset: 'non précisé' }
const CATALOG = {
  archimate: '3.2',
  layers: [{ key: 'business', label: 'Métier' }],
  base: [],
  types: [
    el('BusinessActor', 'Acteur métier'),
    el('BusinessProcess', 'Processus métier'),
    el('BusinessObject', 'Objet métier'),
    rel('Access', 'Accès', 'accède à', 'accédé par', { attributes: [ACCES_ATTR] }),
    rel('Assignment', 'Affectation', 'affecté à', 'mené par'),
    rel('Association', 'Association', 'associé à', 'associé à'),
    rel('Flow', 'Flux', 'transmet à', 'reçoit de'),
  ],
}
const TYPES = [
  { slug: 'acteur', title: 'Acteur', archimate: 'BusinessActor', path: 'p', properties: 0, objects: 1, state: null },
  { slug: 'processus', title: 'Processus de vente', archimate: 'BusinessProcess', path: 'p', properties: 0, objects: 1, state: null },
  { slug: 'document', title: 'Document métier', archimate: 'BusinessObject', path: 'p', properties: 0, objects: 1, state: null },
  { slug: 'acces', title: 'Accès', archimate: 'Access', path: 'p', properties: 0, objects: 1, state: null },
  { slug: 'affectation', title: 'Affectation', archimate: 'Assignment', path: 'p', properties: 0, objects: 0, state: null },
  { slug: 'association', title: 'Association', archimate: 'Association', path: 'p', properties: 0, objects: 1, state: null },
  { slug: 'flux', title: 'Flux', archimate: 'Flow', path: 'p', properties: 0, objects: 1, state: null },
]
const summary = (key: string, type: string, type_title: string, archimate: string, name: string, extra: Record<string, unknown> = {}) => ({ key, type, type_title, archimate, name, excerpt: '', properties: [], relations: 0, is_relation: false, source: null, target: null, path: 'p', dates: null, state: null, ...extra })
const OBJECTS = [
  summary('ACT-CLIENT', 'acteur', 'Acteur', 'BusinessActor', 'Client'),
  summary('PRC-PRISE-COMMANDE', 'processus', 'Processus de vente', 'BusinessProcess', 'Prise de commande'),
  summary('OBJ-COMMANDE', 'document', 'Document métier', 'BusinessObject', 'Commande'),
  summary('REL-FLUX', 'flux', 'Flux', 'Flow', 'Transmet au paiement', { is_relation: true }),
  summary('REL-ECRIT', 'acces', 'Accès', 'Access', 'Écrit la commande', { is_relation: true }),
]
const ACCES_SCHEMA = { type: 'object', additionalProperties: false, properties: { acces: { type: 'string', title: 'Sens', 'x-archimate': true, enum: ['Read', 'Write', 'ReadWrite'], 'x-labels': { Read: 'lecture', Write: 'écriture', ReadWrite: 'lecture-écriture' }, 'x-unset': 'non précisé' } } }
const EMPTY_SCHEMA = { type: 'object', additionalProperties: false, properties: {} }
const choice = (slug: string, title: string, archimate: string, schema = EMPTY_SCHEMA) => ({ slug, title, archimate, object_schema: schema })
// Ce que les règles d'ArchiMate permettent pour chaque couple (source, cible).
const PERMITTED: Record<string, { archimate: string[]; types: ReturnType<typeof choice>[] }> = {
  'ACT-CLIENT>PRC-PRISE-COMMANDE': { archimate: ['Assignment', 'Serving', 'Triggering', 'Flow', 'Association'], types: [choice('affectation', 'Affectation', 'Assignment'), choice('association', 'Association', 'Association'), choice('flux', 'Flux', 'Flow')] },
  'OBJ-COMMANDE>REL-FLUX': { archimate: ['Association'], types: [choice('association', 'Association', 'Association')] },
  'PRC-PRISE-COMMANDE>OBJ-COMMANDE': { archimate: ['Access', 'Association'], types: [choice('acces', 'Accès', 'Access', ACCES_SCHEMA), choice('association', 'Association', 'Association')] },
}
const objectDetail = (key: string, name: string, type: string, type_title: string, archimate: string, relations = { outgoing: [] as unknown[], incoming: [] as unknown[] }) => ({ key, type, type_title, archimate, name, documentation: '', properties: {}, source: null, target: null, path: 'p', schema: EMPTY_SCHEMA, relations, dates: null, state: null, problems: [] })
const ECRIT_ROW = { key: 'REL-ECRIT', name: 'Écrit la commande', type: 'acces', type_title: 'Accès', archimate: 'Access', properties: { acces: 'Write' }, other: { key: 'OBJ-COMMANDE', name: 'Commande', type_title: 'Document métier', exists: true } }

type Call = { method: string; path: string; body?: string }

function setup(url: string) {
  const calls: Call[] = []
  let ecrit: Record<string, unknown> = {
    ...objectDetail('REL-ECRIT', 'Écrit la commande', 'acces', 'Accès', 'Access', {
      outgoing: [],
      incoming: [{ key: 'REL-NOTE', name: 'Note', type: 'association', type_title: 'Association', archimate: 'Association', properties: {}, other: { key: 'OBJ-COMMANDE', name: 'Commande', type_title: 'Document métier', exists: true } }],
    }),
    properties: { acces: 'Write' },
    schema: ACCES_SCHEMA,
    source: 'PRC-PRISE-COMMANDE',
    target: 'OBJ-COMMANDE',
    ends: {
      source: { key: 'PRC-PRISE-COMMANDE', name: 'Prise de commande', type_title: 'Processus de vente', archimate: 'BusinessProcess', is_relation: false, exists: true },
      target: { key: 'OBJ-COMMANDE', name: 'Commande', type_title: 'Document métier', archimate: 'BusinessObject', is_relation: false, exists: true },
    },
    rule: ['Access', 'Association'],
  }
  let prise = objectDetail('PRC-PRISE-COMMANDE', 'Prise de commande', 'processus', 'Processus de vente', 'BusinessProcess', { outgoing: [ECRIT_ROW], incoming: [] })
  vi.stubGlobal(
    'fetch',
    vi.fn((raw: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const [path, query] = raw.split('?')
      calls.push({ method, path: raw, body: init?.body as string | undefined })
      let body: unknown = {}
      const permitted = /\/objets\/([^/]+)\/relations-permises$/.exec(path)
      if (path === '/api/session') body = { authenticated: true, capabilities: ['cartographie:read', 'cartographie:write'] }
      else if (path === `${BASE}/catalogue`) body = CATALOG
      else if (path === `${BASE}/types`) body = { readonly: false, types: TYPES }
      else if (permitted) body = PERMITTED[`${decodeURIComponent(permitted[1])}>${new URLSearchParams(query).get('cible')}`] ?? { archimate: [], types: [] }
      else if (path === `${BASE}/objets` && method === 'GET') body = { readonly: false, objects: OBJECTS }
      else if (path === `${BASE}/modeles`) body = { readonly: false, models: [], rules: [] }
      else if (path === `${BASE}/processus`) body = { readonly: false, processes: [] }
      else if (path === `${BASE}/objets` && method === 'POST') {
        const sent = JSON.parse(String(init?.body)) as Record<string, unknown>
        body = { ...objectDetail(String(sent.key), String(sent.name), String(sent.type), 'Accès', 'Access'), revision: 'draft:1' }
      } else if (path === `${BASE}/objets/REL-ECRIT` && method === 'PUT') {
        const sent = JSON.parse(String(init?.body)) as { properties: Record<string, unknown> }
        ecrit = { ...ecrit, properties: sent.properties }
        prise = { ...prise, relations: { outgoing: [{ ...ECRIT_ROW, properties: sent.properties }], incoming: [] } }
        body = { ...ecrit, revision: 'draft:2' }
      } else if (path === `${BASE}/objets/REL-ECRIT`) body = ecrit
      else if (path === `${BASE}/objets/PRC-PRISE-COMMANDE`) body = prise
      else if (path === `${BASE}/objets/ACT-CLIENT`) body = objectDetail('ACT-CLIENT', 'Client', 'acteur', 'Acteur', 'BusinessActor')
      else if (path === `${BASE}/objets/OBJ-COMMANDE`) body = objectDetail('OBJ-COMMANDE', 'Commande', 'document', 'Document métier', 'BusinessObject')
      else if (path.startsWith(`${BASE}/objets/`)) body = objectDetail(decodeURIComponent(path.split('/').at(-1)!), 'Créée', 'acces', 'Accès', 'Access')
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/cartographie/objets" element={<CartographiePage />} />
          <Route path="/cartographie/objets/:key" element={<CartoObjectPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

async function openRelate(user: ReturnType<typeof userEvent.setup>, heading: string) {
  await screen.findByRole('heading', { name: heading })
  await user.click(screen.getByRole('button', { name: '+ Relier…' }))
  return screen.getByRole('dialog', { name: 'Nouvelle relation' })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Cartographie — relations (ticket b58a0a13)', () => {
  it('d’un acteur vers un processus : affectation proposée, composition absente (critère 1)', async () => {
    setup('/cartographie/objets/ACT-CLIENT')
    const user = userEvent.setup()
    const dialog = await openRelate(user, 'Client')
    await user.click(within(dialog).getByRole('option', { name: /Prise de commande/ }))
    expect(await within(dialog).findByRole('radio', { name: /Affectation/ })).toBeInTheDocument()
    expect(within(dialog).queryByRole('radio', { name: /Composition/ })).not.toBeInTheDocument()
  })

  it('d’un objet métier vers une relation de flux : association proposée (critère 2)', async () => {
    setup('/cartographie/objets/OBJ-COMMANDE')
    const user = userEvent.setup()
    const dialog = await openRelate(user, 'Commande')
    await user.type(within(dialog).getByRole('searchbox'), 'paiement')
    await user.click(within(dialog).getByRole('option', { name: /Transmet au paiement/ }))
    const radios = await within(dialog).findAllByRole('radio')
    expect(radios.map((r) => r.closest('label')?.querySelector('strong')?.textContent)).toEqual(['Association'])
  })

  it('accès : le sens est demandé et écrit avec sa valeur ArchiMate (critères 3 et 6)', async () => {
    const calls = setup('/cartographie/objets/PRC-PRISE-COMMANDE')
    const user = userEvent.setup()
    const dialog = await openRelate(user, 'Prise de commande')
    await user.click(within(dialog).getByRole('option', { name: /Commande OBJ-COMMANDE/ }))
    await user.click(await within(dialog).findByRole('radio', { name: /Accès/ }))
    expect(within(dialog).getByLabelText(/^Clé/)).toHaveValue('REL-PRISE-COMMANDE-COMMANDE')
    expect(within(dialog).getByLabelText(/^Nom/)).toHaveValue('Accède à Commande')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'acces' }), 'écriture')
    expect(within(dialog).getByText('Accès · écriture')).toBeInTheDocument()
    expect(within(dialog).getByText('cartographie/objets/acces/rel-prise-commande-commande.yaml')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Créer la relation' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')!.body!)).toEqual({
      key: 'REL-PRISE-COMMANDE-COMMANDE',
      type: 'acces',
      name: 'Accède à Commande',
      documentation: '',
      properties: { acces: 'Write' },
      source: 'PRC-PRISE-COMMANDE',
      target: 'OBJ-COMMANDE',
    })
  })

  it('fiche de la relation : passer à « lecture », le retour ramène à l’objet (critère 4)', async () => {
    const calls = setup('/cartographie/objets/PRC-PRISE-COMMANDE')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('link', { name: 'Accès Commande' }))
    expect(await screen.findByRole('heading', { name: 'Écrit la commande' })).toBeInTheDocument()
    const band = screen.getByRole('region', { name: 'Source et cible' })
    expect(within(band).getByRole('link', { name: 'Prise de commande' })).toBeInTheDocument()
    expect(within(band).getByText('écriture')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Règle ArchiMate' })).toHaveTextContent('Entre Processus métier et Objet métier')
    await user.selectOptions(screen.getByRole('combobox', { name: 'acces' }), 'lecture')
    expect(within(band).getByText('écriture → lecture')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!).properties).toEqual({ acces: 'Read' })
    await user.click(await screen.findByRole('link', { name: '← Prise de commande' }))
    const row = await screen.findByRole('link', { name: 'Accès Commande' })
    expect(within(row).getByText('lecture')).toBeInTheDocument()
  })

  it('relation cible d’une association : Supprimer grisé, l’association listée (critère 5)', async () => {
    setup('/cartographie/objets/REL-ECRIT')
    await screen.findByRole('heading', { name: 'Écrit la commande' })
    expect(screen.getByRole('button', { name: 'Supprimer' })).toBeDisabled()
    expect(screen.getByRole('note')).toHaveTextContent('REL-NOTE')
    expect(screen.getByRole('region', { name: 'Relations sur cette relation' })).toHaveTextContent('Association')
  })

  it('« Relier… » depuis une vignette', async () => {
    setup('/cartographie/objets')
    const user = userEvent.setup()
    await screen.findByRole('link', { name: 'Client' })
    await user.click(screen.getAllByRole('button', { name: 'Relier…' })[0])
    const dialog = screen.getByRole('dialog', { name: 'Nouvelle relation' })
    expect(within(dialog).getByText('Client')).toBeInTheDocument()
  })
})
