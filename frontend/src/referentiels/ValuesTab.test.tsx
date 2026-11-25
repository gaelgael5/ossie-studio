import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ValuesTab } from './ValuesTab'
import type { RefModel } from './modelRules'

const BASE = '/api/spaces/s1/branches/b1/referentiels/delais/valeurs'
const MODEL: RefModel = {
  title: 'Délais',
  type: 'object',
  'x-key': ['pays', 'mode'],
  required: ['pays', 'mode'],
  properties: {
    pays: { type: 'string' },
    mode: { type: 'string', enum: ['site', 'domicile'] },
    paliers: {
      type: 'array',
      items: {
        type: 'object',
        required: ['poids_max', 'delai_jours'],
        properties: { poids_max: { type: 'number' }, delai_jours: { type: 'integer' } },
      },
    },
  },
}
const VALUES = [
  { pays: 'BE', mode: 'site' },
  { pays: 'FR', mode: 'site', paliers: [{ poids_max: 1, delai_jours: 2 }] },
]

type Problem = { path: string; code: string; detail: string; index: number; field: string }

function setup(
  options: {
    values?: Record<string, unknown>[]
    readOnly?: boolean
    verify?: (values: Record<string, unknown>[]) => Problem[]
  } = {},
) {
  const calls: { method: string; path: string; body?: string }[] = []
  const values = options.values ?? VALUES
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === BASE && method === 'GET') body = { values, count: values.length }
      else if (path === `${BASE}/verification`) {
        const sent = JSON.parse(String(init?.body)).values
        const problems = options.verify ? options.verify(sent) : []
        body = { ok: problems.length === 0, problems }
      } else if (path === BASE && method === 'PUT') body = { ...JSON.parse(String(init?.body)), revision: 'draft:2' }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  const onErrorCount = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ValuesTab
        spaceId="s1"
        branchId="b1"
        name="delais"
        model={MODEL}
        readOnly={options.readOnly ?? false}
        onErrorCount={onErrorCount}
      />
    </QueryClientProvider>,
  )
  return { calls, onErrorCount }
}

function lastPut(calls: { method: string; body?: string }[]) {
  const put = [...calls].reverse().find((c) => c.method === 'PUT')
  return put ? (JSON.parse(put.body!).values as Record<string, unknown>[]) : null
}

afterEach(() => vi.unstubAllGlobals())

describe('Référentiels — onglet Valeurs (ticket fbe04555)', () => {
  it('tableau : colonnes de la clé repérées, tableaux résumés', async () => {
    setup()
    expect(await screen.findByText('BE')).toBeInTheDocument()
    expect(screen.getAllByText('CLÉ')).toHaveLength(2)
    expect(screen.getByText('1 élément ›')).toBeInTheDocument()
    expect(screen.getByText('aucun')).toBeInTheDocument()
  })

  it('ajouter une valeur à deux éléments, puis enregistrer (ordre gardé)', async () => {
    const { calls } = setup()
    await screen.findByText('BE')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '+ Ajouter' }))
    const panel = screen.getByRole('dialog', { name: 'Nouvelle valeur' })
    await user.type(within(panel).getByLabelText('pays'), 'DE')
    await user.selectOptions(within(panel).getByLabelText('mode'), 'domicile')
    await user.click(within(panel).getByRole('button', { name: '+ Ajouter un élément' }))
    await user.click(within(panel).getByRole('button', { name: '+ Ajouter un élément' }))
    await user.type(within(panel).getByLabelText('paliers[0] poids_max'), '30')
    await user.type(within(panel).getByLabelText('paliers[0] delai_jours'), '5')
    await user.type(within(panel).getByLabelText('paliers[1] poids_max'), '1')
    await user.type(within(panel).getByLabelText('paliers[1] delai_jours'), '2')
    // Réordonner : le second élément passe en premier.
    await user.click(within(panel).getByRole('button', { name: 'Monter l’élément 1' }))
    await user.click(within(panel).getByRole('button', { name: 'Appliquer à la valeur' }))
    expect(screen.getByText('1 modification non enregistrée.')).toBeInTheDocument()
    expect(screen.getByText('nouvelle')).toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Enregistrer' })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(lastPut(calls)).not.toBeNull())
    expect(lastPut(calls)).toEqual([
      ...VALUES,
      {
        pays: 'DE',
        mode: 'domicile',
        paliers: [
          { poids_max: 1, delai_jours: 2 },
          { poids_max: 30, delai_jours: 5 },
        ],
      },
    ])
  })

  it('clé déjà présente : erreur sur les champs de la clé, enregistrement bloqué', async () => {
    const { calls } = setup({
      verify: (values) =>
        values.length > 2
          ? [{ path: 'FR+site', code: 'key_duplicate', detail: 'FR+site', index: 2, field: '' }]
          : [],
    })
    await screen.findByText('BE')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '+ Ajouter' }))
    const panel = screen.getByRole('dialog', { name: 'Nouvelle valeur' })
    await user.type(within(panel).getByLabelText('pays'), 'FR')
    await user.selectOptions(within(panel).getByLabelText('mode'), 'site')
    expect(await within(panel).findAllByText('clé déjà présente')).toHaveLength(2)
    await user.click(within(panel).getByRole('button', { name: 'Appliquer à la valeur' }))
    expect(await screen.findByText('erreur')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
    expect(lastPut(calls)).toBeNull()
  })

  it('élément de tableau incomplet : erreur sur la cellule', async () => {
    setup({
      verify: (values) =>
        values.some((v) => Array.isArray(v.paliers) && v.paliers.length === 2)
          ? [
              {
                path: 'FR+site › paliers[1] › delai_jours',
                code: 'value_required',
                detail: '',
                index: 1,
                field: 'paliers[1] › delai_jours',
              },
            ]
          : [],
    })
    await screen.findByText('FR')
    const user = userEvent.setup()
    await user.click(screen.getByText('FR'))
    const panel = screen.getByRole('dialog', { name: 'Valeur FR · site' })
    // Clé figée sur une valeur existante.
    expect(within(panel).getByLabelText('pays')).toHaveAttribute('readonly')
    await user.click(within(panel).getByRole('button', { name: '+ Ajouter un élément' }))
    await user.type(within(panel).getByLabelText('paliers[1] poids_max'), '5')
    expect(await within(panel).findByText('requis')).toBeInTheDocument()
  })

  it('« Annuler les modifications » : rien n’est écrit', async () => {
    const { calls } = setup()
    await screen.findByText('BE')
    const user = userEvent.setup()
    await user.click(screen.getByText('BE'))
    await user.click(screen.getByRole('button', { name: 'Supprimer' }))
    expect(screen.getByText('supprimée')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Annuler les modifications' }))
    expect(screen.queryByText('supprimée')).not.toBeInTheDocument()
    expect(lastPut(calls)).toBeNull()
  })

  it('supprimer une valeur : partie à l’enregistrement', async () => {
    const { calls } = setup()
    await screen.findByText('BE')
    const user = userEvent.setup()
    await user.click(screen.getByText('BE'))
    await user.click(screen.getByRole('button', { name: 'Supprimer' }))
    const save = screen.getByRole('button', { name: 'Enregistrer' })
    await waitFor(() => expect(save).toBeEnabled())
    await user.click(save)
    await waitFor(() => expect(lastPut(calls)).toEqual([VALUES[1]]))
  })

  it('plusieurs centaines de valeurs : pagination et recherche', async () => {
    const many = Array.from({ length: 320 }, (_, i) => ({ pays: `P${String(i).padStart(3, '0')}`, mode: 'site' }))
    setup({ values: many })
    expect(await screen.findByText('Page 1 / 7 — 320 valeur(s)')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(51)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Rechercher'), 'P31')
    expect(screen.getAllByRole('row')).toHaveLength(11)
  })

  it('lecture seule : ni ajout ni enregistrement', async () => {
    setup({ readOnly: true })
    await screen.findByText('BE')
    expect(screen.queryByRole('button', { name: '+ Ajouter' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).not.toBeInTheDocument()
  })

  it('un tableau dans un élément s’ouvre dans le panneau, avec un fil d’Ariane', async () => {
    const nested: RefModel = {
      ...MODEL,
      properties: {
        ...MODEL.properties,
        paliers: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              poids_max: { type: 'number' },
              zones: { type: 'array', items: { type: 'object', properties: { code: { type: 'string' } } } },
            },
          },
        },
      },
    }
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        let body: unknown = {}
        if (path === BASE && method === 'GET')
          body = { values: [{ pays: 'FR', mode: 'site', paliers: [{ poids_max: 1 }, { poids_max: 5, zones: [{ code: 'Z1' }] }] }], count: 1 }
        else if (path === `${BASE}/verification`) body = { ok: true, problems: [] }
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
      }),
    )
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ValuesTab spaceId="s1" branchId="b1" name="delais" model={nested} readOnly={false} onErrorCount={() => undefined} />
      </QueryClientProvider>,
    )
    const user = userEvent.setup()
    await user.click(await screen.findByText('FR'))
    const panel = screen.getByRole('dialog', { name: 'Valeur FR · site' })
    const openButtons = within(panel).getAllByRole('button', { name: 'ouvrir ›' })
    await user.click(openButtons[1])
    const crumbs = within(panel).getByRole('navigation', { name: 'Chemin dans la valeur' })
    expect(crumbs).toHaveTextContent('FR · site › paliers[1]')
    expect(within(panel).getByLabelText('zones[0] code')).toHaveValue('Z1')
    await user.click(within(crumbs).getByRole('button', { name: 'FR · site' }))
    expect(within(panel).getByLabelText('pays')).toHaveValue('FR')
  })
})

