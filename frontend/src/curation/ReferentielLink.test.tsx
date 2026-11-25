import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CurationAssetProperty, ReferentielLink } from '../apiClient'
import { LinkReferentielDialog, ReferentielLinkCell } from './ReferentielLink'

const TARGETS = {
  readonly: false,
  referentiels: [
    {
      name: 'statuts-commande',
      title: 'Statuts de commande',
      schema: 'referentiels/statuts-commande/modele.schema.yaml',
      key: ['code'],
      properties: [
        { name: 'code', type: 'string' },
        { name: 'libelle', type: 'string' },
      ],
    },
    {
      name: 'delais-pays-canal',
      title: 'Délais',
      schema: 'referentiels/delais-pays-canal/modele.schema.yaml',
      key: ['pays', 'canal'],
      properties: [
        { name: 'pays', type: 'string' },
        { name: 'canal', type: 'string' },
        { name: 'delai', type: 'integer' },
      ],
    },
  ],
}

function prop(name: string, referentiel: ReferentielLink | null = null): CurationAssetProperty {
  return {
    name,
    business_name: '',
    type: 'string',
    format: '',
    max_length: null,
    ref: null,
    external: '',
    description: '',
    missing: false,
    referentiel,
  }
}

const LINKED: ReferentielLink = {
  referentiel: 'statuts-commande',
  title: 'Statuts de commande',
  schema: 'referentiels/statuts-commande/modele.schema.yaml',
  property: 'code',
  group: '',
  key_index: 1,
  key_size: 1,
  linked_keys: 1,
  status: 'ok',
  missing: [],
  members: ['statut'],
}

function mockFetch() {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      const body = path.endsWith('/curation-assets/referentiels') ? TARGETS : { ok: true, commit: 'draft:2', fields: [] }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function wrap(node: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>,
  )
}

const PROPS = [prop('statut', LINKED), prop('pays'), prop('canal'), prop('montant')]
const URL = '/api/spaces/s1/branches/b1/sources/app/curation/referentiel-link'

function dialog(property: CurationAssetProperty, properties = PROPS) {
  return wrap(
    <LinkReferentielDialog
      spaceId="s1"
      branchId="b1"
      source="app"
      path="order.schema.yaml"
      property={property}
      properties={properties}
      onClose={() => {}}
    />,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Lier une propriété d’asset à un référentiel (ticket 8269c642)', () => {
  it('clé simple : le chemin du schéma s’affiche et le lien part en une fois', async () => {
    const calls = mockFetch()
    dialog(prop('montant'))
    const user = userEvent.setup()
    const select = await screen.findByLabelText('Référentiel')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    await user.selectOptions(select, 'statuts-commande')
    expect(screen.getByText('referentiels/statuts-commande/modele.schema.yaml')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'code — CLÉ 1/1' })).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Propriété'), 'code')
    await user.click(screen.getByRole('button', { name: 'Lier' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe(URL)
    expect(JSON.parse(put.body!)).toEqual({
      path: 'order.schema.yaml',
      referentiel: 'statuts-commande',
      links: { montant: 'code' },
      replace: null,
    })
  })

  it('clé composée : l’encadré oblige à associer l’autre propriété de la clé', async () => {
    const calls = mockFetch()
    dialog(prop('pays'))
    const user = userEvent.setup()
    const select = await screen.findByLabelText('Référentiel')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    await user.selectOptions(select, 'delais-pays-canal')
    await user.selectOptions(screen.getByLabelText('Propriété'), 'pays')
    expect(screen.getByRole('note')).toHaveTextContent('le couple pays + canal')
    const lier = screen.getByRole('button', { name: 'Lier' })
    expect(lier).toBeDisabled()
    expect(screen.getByText('Associez une propriété de l’asset à : canal.')).toBeInTheDocument()
    const companion = screen.getByLabelText('Propriété de l’asset pour « canal »')
    const options = within(companion).getAllByRole('option').map((o) => o.textContent)
    // Même nom d'abord ; la propriété déjà liée (statut) n'est pas proposée.
    expect(options).toEqual(['— choisir —', 'canal (même nom)', 'montant'])
    await user.selectOptions(companion, 'canal')
    expect(lier).toBeEnabled()
    await user.click(lier)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!).links).toEqual({ pays: 'pays', canal: 'canal' })
  })

  it('propriété hors clé d’un référentiel à clé composée : sans encadré', async () => {
    mockFetch()
    dialog(prop('montant'))
    const user = userEvent.setup()
    const select = await screen.findByLabelText('Référentiel')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    await user.selectOptions(select, 'delais-pays-canal')
    await user.selectOptions(screen.getByLabelText('Propriété'), 'delai')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Lier' })).toBeEnabled()
  })

  it('lien partiel signalé, et délier tout le groupe après confirmation', async () => {
    const calls = mockFetch()
    const partial: ReferentielLink = {
      ...LINKED,
      referentiel: 'delais-pays-canal',
      property: 'pays',
      group: 'g1',
      key_size: 3,
      linked_keys: 2,
      status: 'partial',
      missing: ['zone'],
      members: ['canal', 'pays'],
    }
    wrap(
      <ReferentielLinkCell
        spaceId="s1"
        branchId="b1"
        source="app"
        path="order.schema.yaml"
        property={prop('pays', partial)}
        canWrite
        onLink={() => {}}
      />,
    )
    const user = userEvent.setup()
    const badge = screen.getByRole('button', { name: 'delais-pays-canal · pays · clé 2/3' })
    expect(screen.getByText('Lien partiel : la clé demande aussi zone.')).toBeInTheDocument()
    await user.click(badge)
    await user.click(screen.getByRole('menuitem', { name: 'Délier' }))
    const confirm = screen.getByRole('alertdialog')
    expect(confirm).toHaveTextContent('Délier aussi canal')
    await user.click(within(confirm).getByRole('button', { name: 'Délier le groupe' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
    expect(calls.find((c) => c.method === 'DELETE')!.path).toBe(`${URL}?path=order.schema.yaml&field=pays`)
  })

  it('lecture seule : ni « + Lier », ni menu', () => {
    mockFetch()
    wrap(
      <>
        <ReferentielLinkCell spaceId="s1" branchId="b1" source="app" path="p" property={prop('pays')} canWrite={false} onLink={() => {}} />
        <ReferentielLinkCell spaceId="s1" branchId="b1" source="app" path="p" property={prop('statut', LINKED)} canWrite={false} onLink={() => {}} />
      </>,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('statuts-commande · code')).toBeInTheDocument()
  })
})
