import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DomainDetail } from '../apiClient'
import { DomainLinks } from './DomainLinks'

const BASE = '/api/spaces/s1/branches/b1/domains/expedition'

function field(name: string) {
  return { name, expression: name, datatype: 'Integer', description: '' }
}

const DOMAIN = {
  name: 'expedition',
  label: 'Expédition',
  description: '',
  consultable: 2,
  virtual: 0,
  warnings: [],
  assets: [],
  datatypes: [],
  datasets: [
    { name: 'Colis', consultable: true, derived: false, fields: [field('id'), field('pays'), field('mode')] },
    { name: 'Tarif', consultable: true, derived: false, fields: [field('pays'), field('mode'), field('prix')] },
  ],
  relationships: [{ name: 'ancienne', from_dataset: 'Colis', to_dataset: 'Tarif', from_columns: ['pays'], to_columns: ['pays'] }],
  references: [],
} as unknown as DomainDetail

function setup(canWrite = true) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      calls.push({ method: init?.method ?? 'GET', path, body: init?.body as string | undefined })
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(DOMAIN)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DomainLinks spaceId="s1" branchId="b1" domain={DOMAIN} canWrite={canWrite} />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('Domaine — relations à la main (ticket 4e856094)', () => {
  it('créer une relation à clé composée', async () => {
    const calls = setup()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '+ Nouvelle relation' }))
    await user.type(screen.getByLabelText('Nom de la relation'), 'colis_tarif')
    await user.selectOptions(screen.getByLabelText('Dataset côté un'), 'Tarif')
    await user.selectOptions(screen.getByLabelText('Champ 1 côté plusieurs'), 'pays')
    await user.selectOptions(screen.getByLabelText('Champ 1 côté un'), 'pays')
    await user.click(screen.getByRole('button', { name: '+ Ajouter une paire de champs (clé composée)' }))
    await user.selectOptions(screen.getByLabelText('Champ 2 côté plusieurs'), 'mode')
    await user.selectOptions(screen.getByLabelText('Champ 2 côté un'), 'mode')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const post = calls.find((c) => c.method === 'POST')!
    expect(post.path).toBe(`${BASE}/relationships`)
    expect(JSON.parse(post.body!)).toEqual({
      name: 'colis_tarif',
      from_dataset: 'Colis',
      to_dataset: 'Tarif',
      from_columns: ['pays', 'mode'],
      to_columns: ['pays', 'mode'],
    })
  })

  it('supprimer une relation', async () => {
    const calls = setup()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Supprimer la relation ancienne' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === `${BASE}/relationships/ancienne`)).toBe(true))
  })

  it('sans curation:write : lecture seule', () => {
    setup(false)
    expect(screen.queryByRole('button', { name: '+ Nouvelle relation' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Supprimer la relation ancienne' })).not.toBeInTheDocument()
  })
})
