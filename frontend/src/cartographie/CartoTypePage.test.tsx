import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { CartoTypePage } from './CartoTypePage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

// L'éditeur visuel (JSONJoy) et Monaco sont remplacés par un double, comme dans
// les tests des Référentiels : un champ pour le YAML et un bouton qui simule une
// modification faite dans l'éditeur visuel.
const visualChange = { current: (m: Record<string, unknown>) => m }
vi.mock('../referentiels/SchemaEditor', () => ({
  default: ({
    text,
    parsed,
    readOnly,
    onVisual,
    onYaml,
    before,
    visualTitle,
  }: {
    text: string
    parsed: { ok: boolean; model?: Record<string, unknown> }
    readOnly: boolean
    onVisual: (s: Record<string, unknown>) => void
    onYaml: (t: string) => void
    before?: React.ReactNode
    visualTitle?: string
  }) => (
    <div>
      <h2>{visualTitle}</h2>
      {before}
      <textarea aria-label="YAML" value={text} readOnly={readOnly} onChange={(e) => onYaml(e.target.value)} />
      {parsed.ok ? (
        <button type="button" onClick={() => onVisual(visualChange.current(parsed.model!))}>
          visuel
        </button>
      ) : null}
    </div>
  ),
}))

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 12h20"/></svg>'
const CATALOG = {
  archimate: '3.2',
  layers: [{ key: 'business', label: 'Métier' }],
  base: [
    { name: 'cle', type: 'string', required: true },
    { name: 'nom', type: 'string', required: true },
    { name: 'documentation', type: 'string', required: false },
  ],
  types: [
    { name: 'BusinessProcess', kind: 'element', layer: 'business', label: 'Processus métier', definition: 'Déf.', icon: ICON, attributes: [], relations: [] },
  ],
}
const CANAL = { type: 'string', enum: ['site', 'magasin'], description: 'Canal de la vente' }
function documentOf(properties: Record<string, unknown> = {}, title = 'Processus de vente') {
  const doc: Record<string, unknown> = { title, 'x-archimate': 'BusinessProcess', type: 'object' }
  if (Object.keys(properties).length > 0) doc.properties = properties
  return doc
}
function detailOf(options: { properties?: Record<string, unknown>; usage?: Record<string, number>; objects?: number } = {}) {
  const document = documentOf(options.properties)
  return {
    slug: 'processus-de-vente',
    title: 'Processus de vente',
    archimate: 'BusinessProcess',
    path: 'cartographie/types/processus-de-vente.schema.yaml',
    document,
    yaml: stringify(document),
    objects: options.objects ?? 23,
    usage: options.usage ?? {},
    state: null,
    problems: [],
    relations: [
      { relation: 'flux', title: 'Flux', archimate: 'Flow', archimate_targets: ['processus-de-vente', 'application'] },
      { relation: 'composition', title: 'Composition', archimate: 'Composition', archimate_targets: [] },
    ],
    space_types: [
      { slug: 'application', title: 'Application', archimate: 'ApplicationComponent' },
      { slug: 'processus-de-vente', title: 'Processus de vente', archimate: 'BusinessProcess' },
    ],
  }
}

function setup(options: { capabilities?: string[]; detail?: ReturnType<typeof detailOf> } = {}) {
  const calls: { method: string; path: string; body?: string }[] = []
  const capabilities = options.capabilities ?? ['cartographie:read', 'cartographie:write']
  const detail = options.detail ?? detailOf()
  vi.stubGlobal(
    'fetch',
    vi.fn((rawPath: string, init?: RequestInit) => {
      // En vue publiée, les lectures portent `?view=published` (bug a512d8c3).
      const path = rawPath.split('?')[0]
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path === `${BASE}/catalogue`) body = CATALOG
      else if (path === `${BASE}/types/processus-de-vente` && method === 'GET') body = detail
      else if (path === `${BASE}/types/processus-de-vente` && method === 'PUT') {
        const schema = JSON.parse(String(init?.body)).schema as Record<string, unknown>
        body = { ...detail, document: schema, yaml: stringify(schema), revision: 'draft:2' }
      } else if (path === `${BASE}/types/processus-de-vente` && method === 'DELETE') body = { deleted: 'processus-de-vente', revision: 'draft:3' }
      else if (path === `${BASE}/types`) body = { readonly: false, types: [] }
      else if (path === `${BASE}/types/processus-de-vente/imports`) body = { imports: [] }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/cartographie/types/processus-de-vente']}>
        <Routes>
          <Route path="/cartographie/types" element={<p>liste des types</p>} />
          <Route path="/cartographie/types/:slug" element={<CartoTypePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

function lastPut(calls: { method: string; path: string; body?: string }[]) {
  const put = calls.filter((c) => c.method === 'PUT').at(-1)
  return put ? (JSON.parse(put.body!).schema as Record<string, unknown>) : null
}

afterEach(() => {
  vi.unstubAllGlobals()
  visualChange.current = (m) => m
})

describe('Cartographie — personnaliser un type (ticket 5c39d4df)', () => {
  it('en-tête, type ArchiMate figé et propriétés de base en lecture seule (critère 2)', async () => {
    setup()
    expect(await screen.findByRole('heading', { name: 'Processus de vente' })).toBeInTheDocument()
    expect(screen.getAllByText('Métier').length).toBeGreaterThan(0)
    expect(screen.getByText(/repose sur Processus métier \(ArchiMate\)/)).toBeInTheDocument()
    expect(screen.getByText(/repose sur/)).toHaveTextContent('cartographie/types/processus-de-vente.schema.yaml · 23 objets')
    expect(screen.getByRole('link', { name: '← Retour aux types' })).toHaveAttribute('href', '/cartographie/types')
    const base = screen.getByText('Propriétés de base ArchiMate · lecture seule').closest('.panel') as HTMLElement
    for (const name of ['cle', 'nom', 'documentation']) expect(within(base).getByText(name)).toBeInTheDocument()
    expect(within(base).queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('ajouter canal (énumération) : le YAML et l’enregistrement le portent (critère 1)', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    visualChange.current = (m) => ({ ...m, properties: { canal: CANAL } })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'visuel' }))
    expect((screen.getByLabelText('YAML') as HTMLTextAreaElement).value).toContain('canal:')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(lastPut(calls)).not.toBeNull())
    expect(lastPut(calls)).toEqual({ ...documentOf(), properties: { canal: CANAL } })
  })

  it('renommer change le libellé, pas le slug ni le type ArchiMate (critère 3)', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Renommer' }))
    const input = screen.getByLabelText('Nouveau libellé du type')
    await user.clear(input)
    await user.type(input, 'Vente')
    await user.click(screen.getByRole('button', { name: 'Appliquer' }))
    expect(screen.getByRole('heading', { name: 'Vente' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(lastPut(calls)).not.toBeNull())
    expect(lastPut(calls)).toEqual(documentOf({}, 'Vente'))
    expect(calls.find((c) => c.method === 'PUT')!.path).toBe(`${BASE}/types/processus-de-vente`)
  })

  it('retirer canal utilisé par 23 objets : refusé avant l’envoi (critère 4)', async () => {
    const calls = setup({ detail: detailOf({ properties: { canal: CANAL }, usage: { canal: 23 } }) })
    await screen.findByRole('heading', { name: 'Processus de vente' })
    visualChange.current = (m) => ({ ...m, properties: {} })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'visuel' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Retirer canal : refusé tant que 23 objets l’utilisent.')
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('changer x-archimate dans le YAML : refusé avant l’envoi', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    const user = userEvent.setup()
    const yaml = screen.getByLabelText('YAML')
    await user.clear(yaml)
    await user.type(yaml, 'title: Processus de vente{enter}x-archimate: BusinessEvent{enter}type: object')
    expect(screen.getByRole('alert')).toHaveTextContent('Le type ArchiMate d’un type est figé')
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
  })

  it('une définition ($defs) ajoutée dans l’éditeur visuel : signalée avant l’envoi', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    visualChange.current = (m) => ({ ...m, $defs: { adresse: { type: 'string' } } })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'visuel' }))
    expect(screen.getByRole('alert')).toHaveTextContent('retirez $defs')
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
  })

  it('supprimer : refusé tant qu’il a des objets, sinon retour à la liste', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    expect(screen.getByRole('button', { name: 'Supprimer le type' })).toBeDisabled()
    expect(screen.getByText('Refusé tant que le type a 23 objets.')).toBeInTheDocument()
  })

  it('supprimer un type sans objet', async () => {
    const calls = setup({ detail: detailOf({ objects: 0 }) })
    await screen.findByRole('heading', { name: 'Processus de vente' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Supprimer le type' }))
    await user.click(screen.getByRole('button', { name: 'Supprimer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
    expect(await screen.findByText('liste des types')).toBeInTheDocument()
  })

  it('sans le droit de modifier : rien n’est modifiable', async () => {
    setup({ capabilities: ['cartographie:read'] })
    await screen.findByRole('heading', { name: 'Processus de vente' })
    for (const name of ['Enregistrer', 'Renommer', 'Supprimer le type']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
  })
})

describe('Cartographie — relations permises d’un type (ticket 2643fd8f)', () => {
  it('décocher une cible d’ArchiMate écrit l’écart ; elle reste visible, désactivée', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'Relations' }))
    const target = screen.getByRole('checkbox', { name: 'Flux vers Application' })
    expect(target).toBeChecked()
    // Composition : aucune cible d'ArchiMate, donc proposée dans « + Ajouter une relation ».
    expect(screen.queryByRole('checkbox', { name: /Composition/ })).not.toBeInTheDocument()
    await user.click(target)
    expect(screen.getByRole('checkbox', { name: 'Flux vers Application' })).not.toBeChecked()
    expect(screen.getByText('désactivée')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(lastPut(calls)).not.toBeNull())
    expect(lastPut(calls)!['x-relations']).toEqual({ desactivees: [{ relation: 'flux', cibles: ['application'] }] })
  })

  it('ajouter une relation et une cible hors ArchiMate', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'Relations' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Ajouter une relation' }), 'composition')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Ajouter une cible à Composition' }), 'application')
    expect(screen.getByText('hors ArchiMate')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(lastPut(calls)).not.toBeNull())
    expect(lastPut(calls)!['x-relations']).toEqual({ ajoutees: [{ relation: 'composition', cibles: ['application'] }] })
  })

  it('décocher toute la relation désactive toutes ses cibles ; recocher efface l’écart', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'Relations' }))
    const whole = screen.getByRole('checkbox', { name: 'Activer ou désactiver toute la relation Flux' })
    await user.click(whole)
    expect(screen.getByRole('checkbox', { name: 'Flux vers Application' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Flux vers Processus de vente' })).not.toBeChecked()
    await user.click(whole)
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('sans le droit de modifier : l’onglet est en lecture seule', async () => {
    setup({ capabilities: ['cartographie:read'] })
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await userEvent.setup().click(screen.getByRole('tab', { name: 'Relations' }))
    expect(screen.getByRole('checkbox', { name: 'Flux vers Application' })).toBeDisabled()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })
})

describe('Cartographie — imports d’un type (ticket 49952be0)', () => {
  it('l’onglet Imports liste les imports du type, à son adresse', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await userEvent.setup().click(screen.getByRole('tab', { name: 'Imports' }))
    await waitFor(() => expect(calls.some((c) => c.path === `${BASE}/types/processus-de-vente/imports`)).toBe(true))
    expect(await screen.findByText('Aucun import déclaré.')).toBeInTheDocument()
  })
})

describe('Cartographie — vue « Publiée » en lecture seule (bug a512d8c3)', () => {
  afterEach(() => window.localStorage.removeItem('ossie.view.b1'))

  it('en vue publiée, rien n’est modifiable et une note dit comment modifier', async () => {
    window.localStorage.setItem('ossie.view.b1', 'published')
    setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    expect(screen.getByRole('note')).toHaveTextContent('Passez en vue « Fusionnée »')
    for (const name of ['Enregistrer', 'Renommer', 'Supprimer le type']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
  })
})

