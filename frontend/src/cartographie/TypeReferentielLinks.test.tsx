import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TypeReferentielLinks } from './TypeReferentielLinks'

const URL_ = '/api/spaces/s1/branches/b1/cartographie/types/application/referentiels'
const PAYS = { name: 'pays', title: 'Pays', tags: ['entreprise'], key: [{ name: 'code', type: 'string', title: 'Code' }], error: null }
const SITES = {
  name: 'sites',
  title: 'Sites',
  tags: ['contrainte'],
  key: [
    { name: 'pays', type: 'string', title: 'Pays' },
    { name: 'site', type: 'string', title: 'Site' },
  ],
  error: null,
}
const LINKS = {
  slug: 'application',
  path: 'cartographie/types/application.extend.yaml',
  state: null,
  problems: [],
  links: [{ referentiel: 'pays', proprietes: { pays: 'code' }, target: PAYS, problems: [], absent: 2 }],
  referentiels: [PAYS, SITES],
  properties: [
    { name: 'pays', title: 'pays', type: 'string' },
    { name: 'site_pays', title: 'Pays du site', type: 'string' },
    { name: 'site', title: 'site', type: 'string' },
    { name: 'niveau', title: 'niveau', type: 'integer' },
  ],
}

function setup(readOnly = false) {
  const calls: { method: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      if (path === URL_) calls.push({ method, body: init?.body as string | undefined })
      const body = path === URL_ ? LINKS : {}
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <TypeReferentielLinks spaceId="s1" branchId="b1" slug="application" readOnly={readOnly} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('Type de cartographie — liens vers les référentiels (ticket 43478978)', () => {
  it('le lien existant : référentiel, tags, propriétés, objets signalés', async () => {
    setup()
    const block = await screen.findByRole('region', { name: 'Liens vers les référentiels' })
    expect(await within(block).findByRole('link', { name: 'Pays' })).toHaveAttribute('href', '/referentiels/pays')
    expect(block).toHaveTextContent('entreprise')
    expect(block).toHaveTextContent('pays → code')
    expect(block).toHaveTextContent('2 objets ont une valeur absente de ce référentiel')
  })

  it('ajouter un lien de clé composée, filtré par tag ; une propriété liée ne se propose plus', async () => {
    const calls = setup()
    const add = await screen.findByRole('group', { name: 'Ajouter un lien' })
    const user = userEvent.setup()
    await user.selectOptions(within(add).getByRole('combobox', { name: 'Filtrer par tag' }), 'contrainte')
    const referentiel = within(add).getByRole('combobox', { name: 'Référentiel' })
    expect(within(referentiel).queryByRole('option', { name: /Pays/ })).not.toBeInTheDocument()
    await user.selectOptions(referentiel, 'sites')
    const forPays = within(add).getByRole('combobox', { name: 'Propriété du type pour pays' })
    // `pays` est déjà lié, `niveau` n'est pas une chaîne.
    expect(within(forPays).queryByRole('option', { name: 'pays' })).not.toBeInTheDocument()
    expect(within(forPays).queryByRole('option', { name: 'niveau' })).not.toBeInTheDocument()
    expect(within(add).getByRole('button', { name: 'Lier' })).toBeDisabled()
    await user.selectOptions(forPays, 'site_pays')
    await user.selectOptions(within(add).getByRole('combobox', { name: 'Propriété du type pour site' }), 'site')
    await user.click(within(add).getByRole('button', { name: 'Lier' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)).toEqual({
      links: [
        { referentiel: 'pays', proprietes: { pays: 'code' } },
        { referentiel: 'sites', proprietes: { site_pays: 'pays', site: 'site' } },
      ],
    })
  })

  it('retirer un lien enregistre les autres', async () => {
    const calls = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Retirer le lien' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)).toEqual({ links: [] })
  })

  it('lecture seule : ni ajout ni retrait', async () => {
    setup(true)
    expect(await screen.findByRole('link', { name: 'Pays' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retirer le lien' })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Ajouter un lien' })).not.toBeInTheDocument()
  })
})
