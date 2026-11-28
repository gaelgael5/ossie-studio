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
    after,
    visualTitle,
  }: {
    text: string
    parsed: { ok: boolean; model?: Record<string, unknown> }
    readOnly: boolean
    onVisual: (s: Record<string, unknown>) => void
    onYaml: (t: string) => void
    before?: React.ReactNode
    after?: React.ReactNode
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
      {after}
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
    { name: 'Flow', kind: 'relation', layer: 'relationship', label: 'Flux', definition: 'Déf.', icon: ICON, attributes: [], relations: [] },
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
    vi.fn((path: string, init?: RequestInit) => {
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
      else if (path === `${BASE}/objets`)
        body = {
          readonly: false,
          objects: [
            { key: 'PRC-B', type: 'processus-de-vente', ref: 'processus-de-vente/PRC-B', name: 'Vente B' },
            { key: 'APP-X', type: 'application', ref: 'application/APP-X', name: 'Autre type' },
            { key: 'PRC-A', type: 'processus-de-vente', ref: 'processus-de-vente/PRC-A', name: 'Vente A' },
          ],
        }
      else if (path === `${BASE}/objets/processus-de-vente/PRC-A` && method === 'DELETE') body = { deleted: 'PRC-A', revision: 'draft:4' }
      else if (path === `${BASE}/objets/processus-de-vente/PRC-B` && method === 'DELETE')
        return Promise.resolve({
          ok: false,
          status: 409,
          text: () => Promise.resolve(JSON.stringify({ detail: { code: 'object_in_use', key: 'PRC-B' } })),
        })
      else if (path === `${BASE}/objets/processus-de-vente/PRC-A`) body = { key: 'PRC-A', yaml: 'cle: PRC-A\nnom: Vente A\n' }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/cartographie/types/processus-de-vente']}>
        <Routes>
          <Route path="/cartographie/types" element={<p>liste des types</p>} />
          <Route path="/cartographie/types/:slug" element={<CartoTypePage />} />
          <Route path="/cartographie/objets/:type/:key" element={<p>fiche de l’objet</p>} />
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

/** L'écran s'ouvre sur l'onglet « Résumé » (ticket e2b9d7de). */
async function openProperties() {
  await userEvent.setup().click(screen.getByRole('tab', { name: 'Propriétés' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  visualChange.current = (m) => m
})

describe('Cartographie — personnaliser un type (ticket 5c39d4df)', () => {
  it('s’ouvre sur « Résumé » avec le bandeau ArchiMate ; Supprimer est à droite d’Enregistrer (ticket e2b9d7de)', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    expect(screen.getAllByRole('tab').map((b) => b.textContent)).toEqual(['Résumé', 'Propriétés', 'Relations', 'Imports'])
    expect(screen.getByRole('tab', { name: 'Résumé' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('region', { name: 'Type ArchiMate' })).toHaveTextContent('Processus métier')
    const save = screen.getByRole('button', { name: 'Enregistrer' })
    const remove = screen.getByRole('button', { name: 'Supprimer le type' })
    expect(save.closest('.page-actions')).toBe(remove.closest('.page-actions'))
    expect(save.compareDocumentPosition(remove) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await openProperties()
    expect(screen.queryByRole('region', { name: 'Type ArchiMate' })).not.toBeInTheDocument()
    const added = screen.getByText('Ajoutées sur ce type')
    const base = screen.getByText('Propriétés de base ArchiMate · lecture seule')
    expect(added.compareDocumentPosition(base) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('en-tête, type ArchiMate figé et propriétés de base en lecture seule (critère 2)', async () => {
    setup()
    expect(await screen.findByRole('heading', { name: 'Processus de vente' })).toBeInTheDocument()
    await openProperties()
    expect(screen.getAllByText('Métier').length).toBeGreaterThan(0)
    expect(screen.getByText(/repose sur Processus métier \(ArchiMate\)/)).toBeInTheDocument()
    expect(screen.getByText(/repose sur/)).toHaveTextContent('cartographie/types/processus-de-vente.schema.yaml · 23 objets')
    expect(screen.getByRole('link', { name: '← Retour aux types' })).toHaveAttribute('href', '/cartographie/types')
    const base = screen.getByText('Propriétés de base ArchiMate · lecture seule').closest('.panel') as HTMLElement
    for (const name of ['cle', 'nom', 'documentation']) expect(within(base).getByText(name)).toBeInTheDocument()
    expect(within(base).queryByText('source')).not.toBeInTheDocument()
    expect(within(base).queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('type de relation : source et cible parmi les propriétés de base (bug c5cf543c)', async () => {
    const flow = detailOf()
    setup({ detail: { ...flow, archimate: 'Flow', document: { ...flow.document, 'x-archimate': 'Flow' } } })
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await openProperties()
    const base = screen.getByText('Propriétés de base ArchiMate · lecture seule').closest('.panel') as HTMLElement
    expect(within(base).getByText('source').closest('tr')).toHaveTextContent('clé de l’objet de départ')
    expect(within(base).getByText('cible').closest('tr')).toHaveTextContent('clé de l’objet d’arrivée')
  })

  it('ajouter canal (énumération) : le YAML et l’enregistrement le portent (critère 1)', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await openProperties()
    visualChange.current = (m) => ({ ...m, properties: { canal: CANAL } })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'visuel' }))
    expect((screen.getByLabelText('YAML') as HTMLTextAreaElement).value).toContain('canal:')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(lastPut(calls)).not.toBeNull())
    expect(lastPut(calls)).toEqual({ ...documentOf(), properties: { canal: CANAL } })
  })

  it('changer le libellé par le crayon, pas le slug ni le type ArchiMate (critère 3, ticket 96f508e9 ; ticket 1ad51345)', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    const user = userEvent.setup()
    expect(screen.queryByLabelText('Libellé')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Modifier le libellé' }))
    const input = screen.getByLabelText('Libellé')
    expect(input).toHaveValue('Processus de vente')
    expect(input).toHaveFocus()
    await user.clear(input)
    await user.type(input, 'Vente{enter}')
    expect(screen.queryByLabelText('Libellé')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Vente' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(lastPut(calls)).not.toBeNull())
    expect(lastPut(calls)).toEqual(documentOf({}, 'Vente'))
    expect(calls.find((c) => c.method === 'PUT')!.path).toBe(`${BASE}/types/processus-de-vente`)
  })

  it('libellé : la perte du focus valide, Échap annule, un libellé vide est refusé (ticket 1ad51345)', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await openProperties()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Modifier le libellé' }))
    await user.clear(screen.getByLabelText('Libellé'))
    await user.type(screen.getByLabelText('Libellé'), 'Abandon{escape}')
    expect(screen.queryByLabelText('Libellé')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Processus de vente' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Modifier le libellé' }))
    await user.clear(screen.getByLabelText('Libellé'))
    await user.type(screen.getByLabelText('Libellé'), 'Ventes')
    await user.click(screen.getByRole('heading', { name: /Propriétés/ }).parentElement ?? document.body)
    expect(screen.queryByLabelText('Libellé')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Ventes' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Modifier le libellé' }))
    await user.clear(screen.getByLabelText('Libellé'))
    await user.keyboard('{enter}')
    expect(screen.getByRole('heading', { name: 'processus-de-vente' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
  })

  it('retirer canal utilisé par 23 objets : refusé avant l’envoi (critère 4)', async () => {
    const calls = setup({ detail: detailOf({ properties: { canal: CANAL }, usage: { canal: 23 } }) })
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await openProperties()
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
    await openProperties()
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
    await openProperties()
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
    expect(screen.queryByLabelText('Libellé')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifier le libellé' })).not.toBeInTheDocument()
  })
})

describe('Cartographie — aller sur Imports avec une définition non enregistrée (bugs f1a6758d, 9cf80cdb)', () => {
  const modify = async () => {
    await openProperties()
    visualChange.current = (m) => ({ ...m, properties: { canal: CANAL } })
    await userEvent.setup().click(screen.getByRole('button', { name: 'visuel' }))
  }

  it('sans modification : Imports s’ouvre sans fenêtre', async () => {
    setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await userEvent.setup().click(screen.getByRole('tab', { name: 'Imports' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Imports' })).toHaveAttribute('aria-selected', 'true')
  })

  it('Rester : la fenêtre se ferme, l’onglet et la modification restent', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await modify()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'Imports' }))
    expect(screen.getByRole('dialog', { name: 'Modifications non enregistrées' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Rester' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Propriétés' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeEnabled()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('Enregistrer et continuer : la définition est enregistrée, puis Imports s’ouvre', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await modify()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'Imports' }))
    await user.click(screen.getByRole('button', { name: 'Enregistrer et continuer' }))
    await waitFor(() => expect(lastPut(calls)).toEqual({ ...documentOf(), properties: { canal: CANAL } }))
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Imports' })).toHaveAttribute('aria-selected', 'true'))
  })

  it('Continuer sans enregistrer : la modification est abandonnée, Imports s’ouvre', async () => {
    const calls = setup()
    await screen.findByRole('heading', { name: 'Processus de vente' })
    await modify()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'Imports' }))
    await user.click(screen.getByRole('button', { name: 'Continuer sans enregistrer' }))
    expect(screen.getByRole('tab', { name: 'Imports' })).toHaveAttribute('aria-selected', 'true')
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
    await user.click(screen.getByRole('tab', { name: 'Propriétés' }))
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
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

  it('« Résumé » liste les objets du type (clé, nom) ; la ligne cliquée montre son YAML (ticket 7dc220a4)', async () => {
    setup()
    const table = await screen.findByRole('table', { name: 'Objets de ce type' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => [...r.querySelectorAll('td')].slice(0, 2).map((c) => c.textContent).join(' '))).toEqual([
      'PRC-A Vente A',
      'PRC-B Vente B',
    ])
    expect(screen.getByText('Cliquez sur une ligne pour afficher l’objet.')).toBeInTheDocument()
    await userEvent.setup().click(rows[0])
    expect(await screen.findByLabelText('Fichier YAML de PRC-A')).toHaveTextContent('cle: PRC-A nom: Vente A')
    expect(rows[0]).toHaveClass('selected')
  })

  it('« Résumé » : le filtre porte sur la clé et le nom, sans casse ni accents (ticket b531abe2)', async () => {
    setup()
    const table = await screen.findByRole('table', { name: 'Objets de ce type' })
    const user = userEvent.setup()
    const search = screen.getByRole('searchbox', { name: 'Rechercher par nom ou par clé…' })
    const keys = () => within(table).queryAllByRole('row').slice(1).map((r) => r.querySelector('td')?.textContent)
    await user.type(search, 'prc-b')
    expect(keys()).toEqual(['PRC-B'])
    expect(screen.getByRole('heading', { name: '1 objet sur 2' })).toBeInTheDocument()
    await user.clear(search)
    await user.type(search, 'VÉNTE A')
    expect(keys()).toEqual(['PRC-A'])
    await user.clear(search)
    await user.type(search, 'zzz')
    expect(screen.getByText('Aucun objet ne correspond à la recherche.')).toBeInTheDocument()
    await user.clear(search)
    expect(keys()).toEqual(['PRC-A', 'PRC-B'])
  })

  it('le crayon ouvre la fiche de l’objet sans sélectionner la ligne (ticket 61a24b85)', async () => {
    setup()
    await screen.findByRole('table', { name: 'Objets de ce type' })
    await userEvent.setup().click(screen.getByRole('link', { name: 'Ouvrir la fiche de PRC-A' }))
    expect(await screen.findByText('fiche de l’objet')).toBeInTheDocument()
  })

  it('la corbeille demande confirmation puis supprime l’objet (ticket 61a24b85)', async () => {
    const calls = setup()
    const user = userEvent.setup()
    const table = await screen.findByRole('table', { name: 'Objets de ce type' })
    await user.click(within(table).getAllByRole('row')[1])
    await screen.findByLabelText('Fichier YAML de PRC-A')
    await user.click(screen.getByRole('button', { name: 'Supprimer PRC-A' }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Confirmer la suppression' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === `${BASE}/objets/processus-de-vente/PRC-A`)).toBe(true))
    expect(await screen.findByText('Cliquez sur une ligne pour afficher l’objet.')).toBeInTheDocument()
  })

  it('la corbeille : un refus du serveur s’affiche, la liste reste (ticket 61a24b85)', async () => {
    setup()
    const user = userEvent.setup()
    await screen.findByRole('table', { name: 'Objets de ce type' })
    await user.click(screen.getByRole('button', { name: 'Supprimer PRC-B' }))
    await user.click(within(screen.getByRole('table', { name: 'Objets de ce type' })).getByRole('button', { name: 'Annuler' }))
    expect(screen.queryByRole('button', { name: 'Confirmer la suppression' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Supprimer PRC-B' }))
    await user.click(screen.getByRole('button', { name: 'Confirmer la suppression' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Objets de ce type' })).toBeInTheDocument()
  })

  it('en lecture seule : le crayon, pas de corbeille (ticket 61a24b85)', async () => {
    setup({ capabilities: ['cartographie:read'] })
    await screen.findByRole('table', { name: 'Objets de ce type' })
    expect(screen.getByRole('link', { name: 'Ouvrir la fiche de PRC-A' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Supprimer PRC-A' })).not.toBeInTheDocument()
  })

})
