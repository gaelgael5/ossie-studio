import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SourceItem } from '../apiClient'
import { SourceContractsSearch, openapiChosen } from './SourceContractsSearch'

function source(parsers?: string[]): SourceItem {
  return {
    slug: 'colis-21',
    ok: true,
    label: 'colis 21',
    type: 'git',
    connection: { slug: 'forge', label: 'Forge' },
    parameters: { repository: 'o/colis21', branch: 'main', ...(parsers ? { parsers } : {}) },
  } as unknown as SourceItem
}

const FOUND = {
  contracts: [
    { path: 'old/swagger.yaml', status: 'unsupported', title: 'Vieille', version: '0.9', openapi: '2.0', error: '' },
    {
      path: 'services/commandes/openapi.yaml',
      status: 'supported',
      title: 'API Commandes',
      version: '1.2.0',
      openapi: '3.0.3',
      error: '',
    },
    { path: 'broken/openapi.yaml', status: 'invalid', title: '', version: '', openapi: '', error: 'ref_missing' },
  ],
}

function setup(src: SourceItem, payload: unknown = FOUND) {
  const calls: { method: string; url: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ method: init?.method ?? 'GET', url })
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(payload)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SourceContractsSearch spaceId="s1" branchId="b1" source={src} />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('SourceContractsSearch', () => {
  it('liste les contrats trouvés avec leur état, sans rien chercher avant le clic', async () => {
    const calls = setup(source(['csharp', 'openapi']))
    expect(calls).toHaveLength(0)
    expect(screen.queryByText(/n’est pas choisi/)).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Rechercher les contrats' }))
    expect(await screen.findByText('services/commandes/openapi.yaml')).toBeInTheDocument()
    expect(calls[0]).toEqual({ method: 'POST', url: '/api/spaces/s1/branches/b1/sources/colis-21/openapi-search' })
    expect(screen.getByText('API Commandes')).toBeInTheDocument()
    expect(screen.getByText('Pris en charge')).toBeInTheDocument()
    expect(screen.getByText('Non pris en charge (Swagger 2.0)')).toBeInTheDocument()
    expect(screen.getByText('ref_missing')).toBeInTheDocument()
  })

  it('dit quand aucun contrat n’est trouvé', async () => {
    setup(source(), { contracts: [] })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Rechercher les contrats' }))
    expect(await screen.findByText('Aucun contrat OpenAPI trouvé dans le dépôt.')).toBeInTheDocument()
  })

  it('prévient quand le parseur OpenAPI n’est pas choisi sur la source', () => {
    setup(source(['csharp']))
    expect(screen.getByText(/n’est pas choisi sur cette source/)).toBeInTheDocument()
  })
})

describe('openapiChosen', () => {
  it('vrai sans liste (tous les parseurs) ou si openapi y figure', () => {
    expect(openapiChosen(source())).toBe(true)
    expect(openapiChosen(source(['openapi']))).toBe(true)
    expect(openapiChosen(source(['csharp']))).toBe(false)
  })
})
