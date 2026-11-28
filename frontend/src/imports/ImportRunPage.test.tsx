import { render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CartoImportRunPage } from './ImportRunPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

const BASE = '/api/spaces/s1/branches/b1/cartographie/types/document/imports'
const RUN = {
  id: 41,
  owner: 'document',
  slug: 'fichier',
  label: 'Fichier des documents',
  at: '2026-11-28T10:00:00+00:00',
  by: 'Camille <camille@example.org>',
  file: 'documents.csv',
  deletion: 'all',
  deletion_label: 'On supprime tous les éléments absents',
  outcome: 'imported',
  counts: { new: 1, modified: 0, deleted: 0, kept: 1 },
  kept_in_use: [
    {
      key: 'DOC-Y',
      ref: 'document/DOC-Y',
      usages: [
        {
          ref: 'acces/REL-1',
          key: 'REL-1',
          name: 'Lit le document',
          type: 'acces',
          type_title: 'Accès',
          role: 'target',
          other: { ref: 'processus/PRC-X', key: 'PRC-X', name: 'Prise de commande', type_title: 'Processus' },
        },
      ],
    },
  ],
}

function setup() {
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      const body = path === `${BASE}/fichier/executions/41` ? RUN : {}
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/cartographie/types/document/imports/fichier/executions/41']}>
        <Routes>
          <Route path="/cartographie/types/:slug/imports/:importSlug/executions/:runId" element={<CartoImportRunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Imports — éléments gardés car utilisés (ticket 6d36073b)', () => {
  it('en-tête de l’exécution, et chaque élément expliqué avec ses liens', async () => {
    setup()
    expect(await screen.findByRole('heading', { name: 'Éléments gardés car utilisés — Fichier des documents' })).toBeInTheDocument()
    const header = screen.getByRole('region', { name: 'Exécution de l’import' })
    expect(header).toHaveTextContent('documents.csv')
    expect(header).toHaveTextContent('Camille')
    expect(header).toHaveTextContent('On supprime tous les éléments absents')
    const item = screen.getByRole('region', { name: 'DOC-Y' })
    expect(within(item).getByRole('link', { name: 'DOC-Y' })).toHaveAttribute('href', '/cartographie/objets/document/DOC-Y')
    expect(item).toHaveTextContent(
      'absent du fichier documents.csv ; la stratégie « On supprime tous les éléments absents » prévoit de supprimer les éléments absents.',
    )
    expect(item).toHaveTextContent('il est encore utilisé par 1 relation.')
    const usages = within(item).getByRole('list', { name: 'Utilisations' })
    expect(within(usages).getByRole('link', { name: 'Lit le document' })).toHaveAttribute('href', '/cartographie/objets/acces/REL-1')
    expect(within(usages).getByRole('link', { name: 'Prise de commande' })).toHaveAttribute(
      'href',
      '/cartographie/objets/processus/PRC-X',
    )
    expect(usages).toHaveTextContent('Accès Lit le document depuis Prise de commande (Processus)')
    expect(item).toHaveTextContent('Supprimer ou modifier ces relations, puis relancer l’import')
    expect(screen.getByRole('link', { name: '← Retour à l’import' })).toHaveAttribute('href', '/cartographie/types/document')
  })
})
