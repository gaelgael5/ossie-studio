import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DomainPage, DomainsPage } from './DomainsPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1/domains'
const DETAIL = {
  name: 'expedition',
  label: 'Expédition',
  description: 'Colis',
  consultable: 0,
  virtual: 2,
  assets: [],
  warnings: [],
  datasets: [
    { name: 'Order', source: 'raw:app/order.schema.yaml', consultable: false, derived: true, asset: { source: 'app', path: 'order.schema.yaml' }, description: '', fields: [{ name: 'id' }], primary_key: [] },
    { name: 'Customer', source: 'raw:app/customer.schema.yaml', consultable: false, derived: true, asset: { source: 'app', path: 'customer.schema.yaml' }, description: '', fields: [], primary_key: [] },
  ],
  relationships: [],
  references: [{ name: 'Order_customer', from_dataset: 'Order', field: 'customer', to_dataset: 'Customer', kind: 'field', cardinality: 'n:1' }],
  datatypes: ['String', 'Integer', 'Decimal', 'Float', 'Boolean', 'Date', 'Time', 'DateTime', 'DateTimeTz', 'Opaque'],
}
const MANUAL = {
  name: 'Tarif',
  source: 'logistique.public.tarif',
  consultable: true,
  derived: false,
  asset: null,
  description: '',
  fields: [{ name: 'code', expression: 'code', description: '', label: '', datatype: 'String', property: '' }],
  primary_key: ['code'],
  unique_keys: [],
}

function setup(
  url: string,
  domains: unknown[] = [],
  options: { detail?: unknown; impactsOnce?: boolean } = {},
) {
  let impacts = options.impactsOnce ?? false
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      let status = method === 'DELETE' ? 204 : 200
      if (path.startsWith(`${BASE}/expedition/datasets`) && !path.includes('/referentiel-link') && method !== 'DELETE') {
        const sent = JSON.parse(String(init?.body))
        if (impacts && !sent.confirm) {
          impacts = false
          status = 409
          body = {
            error: 'dataset_change_impacts',
            error_args: { impacts: [{ kind: 'relationship', name: 'sac_tarif', effect: 'follows' }] },
          }
        } else body = { dataset: { ...MANUAL, ...sent }, impacts: [] }
      } else if (path.endsWith('/curation-assets/referentiels')) {
        body = {
          readonly: false,
          referentiels: [
            { name: 'pays', title: 'Pays', schema: 'referentiels/pays/modele.schema.yaml', key: ['code'], properties: [{ name: 'code', type: 'string' }] },
          ],
        }
      } else if (path.includes('/referentiel-link')) {
        body = { ok: true, fields: ['code'] }
      } else if (path === '/api/session') body = { authenticated: true, capabilities: ['curation:read', 'curation:write'] }
      else if (path === BASE && method === 'GET') body = { domains }
      else if (path === BASE) body = { ...DETAIL, datasets: undefined }
      else if (path === `${BASE}/expedition` && method === 'PATCH') body = { ...DETAIL, ...JSON.parse(String(init?.body)) }
      else if (path === `${BASE}/expedition`) body = options.detail ?? DETAIL
      return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(status === 204 ? '' : JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/domaines" element={<DomainsPage />} />
          <Route path="/domaines/:name" element={<DomainPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('Domaines (tickets a7baaaa3, 36d35a88)', () => {
  it('liste vide puis création', async () => {
    const calls = setup('/domaines')
    expect(await screen.findByText(/Aucun domaine/)).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '+ Nouveau domaine' }))
    await user.type(screen.getByLabelText('Libellé'), 'Point relais PUDO')
    expect(screen.getByLabelText('Slug')).toHaveValue('point-relais-pudo')
    await user.click(screen.getByRole('button', { name: 'Créer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === BASE)).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')!.body!)).toEqual({
      name: 'point-relais-pudo',
      label: 'Point relais PUDO',
      description: '',
    })
  })

  it('toute la ligne de la liste ouvre le domaine', async () => {
    setup('/domaines', [{ ...DETAIL, datasets: undefined }])
    const user = userEvent.setup()
    await user.click(await screen.findByText('Colis'))
    expect((await screen.findAllByText('Order')).length).toBeGreaterThan(0)
  })

  it('« Éditer » change libellé et description, sans ouvrir le domaine', async () => {
    const calls = setup('/domaines', [{ ...DETAIL, datasets: undefined }])
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Éditer le domaine Expédition' }))
    // Le bouton n'a pas ouvert le domaine : la liste est toujours là.
    expect(screen.getByRole('dialog', { name: 'Éditer le domaine Expédition' })).toBeInTheDocument()
    expect(screen.getByText('expedition', { selector: 'code' })).toBeInTheDocument()
    const label = screen.getByLabelText('Libellé')
    await user.clear(label)
    await user.type(label, 'Pudo au colis')
    const desc = screen.getByLabelText('Description')
    await user.clear(desc)
    await user.type(desc, 'Vue par le colis')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PATCH')!.body!)).toEqual({
      label: 'Pudo au colis',
      description: 'Vue par le colis',
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('écran du domaine : datasets virtuels, liens, retrait d’un dataset', async () => {
    const calls = setup('/domaines/expedition')
    expect(await screen.findByText('Datasets virtuels (2)')).toBeInTheDocument()
    expect(screen.getByText('Order.customer → Customer (référence, n:1)')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Voir l’asset' })[0]).toHaveAttribute(
      'href',
      '/curation?source=app&path=order.schema.yaml',
    )
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Retirer Customer du domaine' }))
    // Customer a des liens : l'écran le dit et demande de confirmer (ticket 4e856094).
    const confirm = await screen.findByRole('alertdialog', { name: 'Retirer Customer du domaine' })
    expect(confirm).toHaveTextContent('retire aussi ses 1 relation(s) et référence(s)')
    await user.click(within(confirm).getByRole('button', { name: 'Retirer' }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'DELETE' && c.path === `${BASE}/expedition/datasets/Customer`)).toBe(true),
    )
  })

  it('champs d’un dataset dérivé : lien hérité de l’asset, partiel signalé (ticket 65235f64)', async () => {
    const link = (property: string, extra: Record<string, unknown> = {}) => ({
      referentiel: 'delais', title: 'Délais', schema: 'referentiels/delais/modele.schema.yaml', property,
      group: 'g1', key_index: 1, key_size: 2, linked_keys: 2, status: 'ok', missing: [], members: [property],
      inherited: true, ...extra,
    })
    const order = {
      ...DETAIL.datasets[0],
      fields: [
        { name: 'statut', expression: 'statut', description: '', label: '', datatype: 'String', property: 'statut', referentiel: { ...link('code'), referentiel: 'statuts', key_size: 1, group: '' } },
        { name: 'pays', expression: 'pays', description: '', label: '', datatype: 'String', property: 'pays', referentiel: link('pays', { status: 'partial', linked_keys: 1, missing: ['canal'] }) },
        { name: 'montant', expression: 'montant', description: '', label: '', datatype: 'Decimal', property: 'montant', referentiel: null },
      ],
    }
    setup('/domaines/expedition', [], { detail: { ...DETAIL, datasets: [order] } })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Champs de Order' }))
    const table = screen.getByRole('table', { name: 'Champs de Order' })
    expect(table).toHaveTextContent('statuts · code')
    expect(table).toHaveTextContent('delais · pays · clé 1/2')
    expect(table).toHaveTextContent('Lien partiel : la clé demande aussi canal.')
    expect(screen.getAllByText('hérité de l’asset')).toHaveLength(2)
    // Lecture seule : aucun bouton pour lier ou délier sur un dataset dérivé.
    expect(screen.queryByRole('button', { name: /Lier/ })).not.toBeInTheDocument()
  })

  it('dataset manuel : lier un champ avec la popup de la Curation, délier (ticket 80bb56af)', async () => {
    const linked = {
      ...MANUAL,
      fields: [
        ...MANUAL.fields,
        {
          name: 'pays', expression: 'pays', description: '', label: '', datatype: 'String', property: '',
          referentiel: { referentiel: 'pays', title: 'Pays', schema: 'referentiels/pays/modele.schema.yaml', property: 'code', group: '', key_index: 1, key_size: 1, linked_keys: 1, status: 'ok', missing: [], members: ['pays'], inherited: false },
        },
      ],
    }
    const calls = setup('/domaines/expedition', [], { detail: { ...DETAIL, consultable: 1, datasets: [linked] } })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Champs de Tarif' }))
    await user.click(screen.getByRole('button', { name: 'Lier code à un référentiel' }))
    const dialog = await screen.findByRole('dialog', { name: /référentiel/i })
    expect(dialog).toBeInTheDocument()
    await user.selectOptions(await screen.findByLabelText('Référentiel'), 'pays')
    await user.selectOptions(screen.getByLabelText('Propriété'), 'code')
    await user.click(screen.getByRole('button', { name: 'Lier' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path.endsWith('/datasets/Tarif/referentiel-link'))).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)).toEqual({
      referentiel: 'pays',
      links: { code: 'code' },
      replace: null,
    })
    // Le champ déjà lié : étiquette, puis Délier.
    await user.click(screen.getByRole('button', { name: 'pays · code' }))
    await user.click(screen.getByRole('menuitem', { name: 'Délier' }))
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === 'DELETE' && c.path.endsWith('/datasets/Tarif/referentiel-link?field=pays')),
      ).toBe(true),
    )
  })

  it('dataset manuel : création consultable, types servis par le serveur (ticket 1c68a20a)', async () => {
    const calls = setup('/domaines/expedition')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '+ Dataset manuel' }))
    const dialog = screen.getByRole('dialog', { name: 'Nouveau dataset manuel' })
    expect(dialog).toBeInTheDocument()
    await user.type(screen.getByLabelText('Nom'), 'Tarif')
    await user.type(screen.getByLabelText('Source'), 'logistique.public.tarif')
    await user.type(screen.getAllByLabelText('Champ')[0], 'code')
    await user.type(screen.getAllByLabelText('Expression (colonne ou SQL)')[0], 'code')
    const type = screen.getAllByLabelText('Type')[0]
    expect([...(type as HTMLSelectElement).options].map((o) => o.value)).toContain('DateTimeTz')
    await user.selectOptions(type, 'String')
    await user.click(screen.getByLabelText('code dans la clé primaire'))
    await user.click(screen.getByRole('button', { name: '+ Champ' }))
    await user.type(screen.getAllByLabelText('Champ')[1], 'cree_le')
    await user.type(screen.getAllByLabelText('Expression (colonne ou SQL)')[1], 'created_at')
    await user.selectOptions(screen.getAllByLabelText('Type')[1], 'DateTimeTz')
    await user.click(screen.getByRole('button', { name: 'Créer le dataset' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === `${BASE}/expedition/datasets`)).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')!.body!)).toEqual({
      name: 'Tarif',
      consultable: true,
      source: 'logistique.public.tarif',
      description: '',
      fields: [
        { name: 'code', expression: 'code', datatype: 'String', description: '', previous: '' },
        { name: 'cree_le', expression: 'created_at', datatype: 'DateTimeTz', description: '', previous: '' },
      ],
      primary_key: ['code'],
      unique_keys: [],
      confirm: false,
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('dataset manuel : virtuel sans source ni expression, source manuel: affichée', async () => {
    const calls = setup('/domaines/expedition')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '+ Dataset manuel' }))
    await user.type(screen.getByLabelText('Nom'), 'Suivi')
    await user.click(screen.getByLabelText(/Consultable/))
    expect(screen.queryByLabelText('Source')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Expression (colonne ou SQL)')).not.toBeInTheDocument()
    expect(screen.getByText('manuel:expedition/Suivi')).toBeInTheDocument()
    await user.type(screen.getAllByLabelText('Champ')[0], 'etape')
    await user.click(screen.getByRole('button', { name: 'Créer le dataset' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const sent = JSON.parse(calls.find((c) => c.method === 'POST')!.body!)
    expect([sent.consultable, sent.source, sent.fields[0].expression]).toEqual([false, '', ''])
  })

  it('dataset manuel : renommer un champ lié montre les liens touchés, puis confirme', async () => {
    const detail = { ...DETAIL, datasets: [...DETAIL.datasets, MANUAL] }
    const calls = setup('/domaines/expedition', [], { detail, impactsOnce: true })
    const user = userEvent.setup()
    // Un dataset dérivé n'a pas de bouton « Modifier » ; le manuel, si.
    expect(await screen.findByRole('button', { name: 'Modifier le dataset Tarif' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifier le dataset Order' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Modifier le dataset Tarif' }))
    const name = screen.getAllByLabelText('Champ')[0]
    await user.clear(name)
    await user.type(name, 'ident')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    expect(await screen.findByText('Relation « sac_tarif » : suit le renommage')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer quand même' }))
    await waitFor(() => expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(2))
    const [first, second] = calls.filter((c) => c.method === 'PUT').map((c) => JSON.parse(c.body!))
    expect(calls.find((c) => c.method === 'PUT')!.path).toBe(`${BASE}/expedition/datasets/Tarif`)
    expect(first.fields[0]).toEqual({ name: 'ident', expression: 'code', datatype: 'String', description: '', previous: 'code' })
    // La clé primaire suit le champ renommé.
    expect([first.primary_key, first.confirm, second.confirm]).toEqual([['ident'], false, true])
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})
