import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ValidationLog } from './ValidationLog'

afterEach(() => vi.unstubAllGlobals())

describe('ValidationLog (ticket d7139b17)', () => {
  it('erreurs bloquantes et informations, sans acceptation ; lien vers le document en cause', async () => {
    const methods: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((_path: string, init?: RequestInit) => {
        methods.push(init?.method ?? 'GET')
        const body = {
          blocking: 2,
          entries: [
            { level: 'error', path: 'cartographie/objets/acces/rel-01.yaml', rule: 'references', message_code: 'reference_unknown', args: { kind: 'carto_objet', ref: 'document/DOC-01' }, blocking: true, document_type: 'carto_objet', identity: { type: 'acces', key: 'REL-01' } },
            { level: 'error', path: 'domaines/ventes.ossie.yaml', rule: 'dataset_description', message_code: 'dataset_description_missing', args: { dataset: 'commandes' }, blocking: true, document_type: 'dataset', identity: { domain: 'ventes', name: 'commandes' } },
            { level: 'warning', path: 'sources/s/raw/O.schema.yaml', rule: 'parseable', message_code: 'file_unreadable', args: {}, blocking: false, document_type: 'schema_raw', identity: null },
          ],
        }
        return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
      }),
    )
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <ValidationLog spaceId="s1" branchId="b1" />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(screen.getByText('Journal de validation — erreurs bloquantes : 2')).toBeInTheDocument())
    expect(screen.getByText('référence introuvable : document/DOC-01 (carto_objet)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'cartographie/objets/acces/rel-01.yaml' })).toHaveAttribute('href', '/cartographie/objets/acces/REL-01')
    expect(screen.getByText('dataset commandes sans description : ajoutez-la dans le domaine')).toBeInTheDocument()
    expect(screen.getAllByText('bloquant')).toHaveLength(2)
    expect(screen.getByText('informatif')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(methods).toEqual(['GET'])
  })
})
