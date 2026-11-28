import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PublishPage } from './PublishPage'

const DRAFT = {
  count: 1,
  revision: 2,
  changed_upstream_count: 0,
  objects: [{ path: 'sources/s/raw/O.schema.yaml', state: 'modified', changed_upstream: false }],
}

const CHANGES = {
  count: 1,
  mode: 'origin',
  sources: [
    {
      slug: 's',
      label: 'Source S',
      raw: [{ file: 'O.schema.yaml', title: 'Commande', state: 'modified', folder: 'api', path: 'sources/s/raw/O.schema.yaml' }],
      assets: [],
      orphans: [{ file: 'ghost.schema.yaml', title: 'ghost.schema.yaml', state: 'orphan', folder: null, path: null }],
      other: [],
    },
  ],
  other: [],
}

function mockApi(readiness: { ok: boolean; reasons: string[]; conflicts: string[] }, options: { changesFail?: boolean } = {}) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path.endsWith('/raw-explorer/changes') && options.changesFail)
        return Promise.resolve({ ok: false, status: 403, text: () => Promise.resolve(JSON.stringify({ error: 'forbidden' })) })
      if (path.endsWith('/raw-explorer/changes')) body = CHANGES
      else if (path.includes('/draft/merge'))
        body = {
          path: 'sources/s/raw/O.schema.yaml', state: 'modified', changed_upstream: true, readonly: true, collected: true,
          kind: 'conflict', structured: true, base: { a: 1 }, theirs: { a: 3 }, mine: { a: 2 }, theirs_absent: false,
          mine_absent: false, proposed: null, conflicts: [{ pointer: '/a', base: 1, theirs: 3, mine: 2 }],
        }
      else if (path.endsWith('/draft')) body = DRAFT
      else if (path.endsWith('/readiness')) body = readiness
      else if (path.endsWith('/publish')) body = { commit: 'abc', published: 1 }
      else if (path.endsWith('/impacts')) body = { impacts: [], pending: 0 }
      else if (path.endsWith('/validation')) body = { entries: [], blocking: 0 }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/spaces/s1/branches/b1/publish']}>
        <Routes>
          <Route path="/spaces/:spaceId/branches/:branchId/publish" element={<PublishPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('PublishPage', () => {
  it('bouton inactif tant qu’une raison bloque, raisons affichées', async () => {
    mockApi({ ok: false, reasons: ['impacts_pending', 'credential_missing'], conflicts: [] })
    renderPage()
    await waitFor(() =>
      expect(screen.getByText('Des impacts restent à corriger ou à marquer « vérifié ».')).toBeInTheDocument(),
    )
    expect(screen.getByText('Votre jeton personnel n’est pas saisi.')).toBeInTheDocument()
    expect(screen.getByText('Pour publier, saisissez puis testez votre jeton personnel.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publier' })).toBeDisabled()
  })

  it('publie avec le commentaire quand rien ne bloque', async () => {
    const calls = mockApi({ ok: true, reasons: [], conflicts: [] })
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('button', { name: 'Déplier Sources' })
    const button = screen.getByRole('button', { name: 'Publier' })
    expect(button).toBeDisabled() // commentaire vide
    await user.type(screen.getByLabelText('Commentaire du commit'), 'Ma modif')
    await user.click(button)
    await waitFor(() => expect(screen.getByText('Publié : 1 objet(s) en un commit.')).toBeInTheDocument())
    const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/publish'))
    expect(JSON.parse(post?.body ?? '{}')).toEqual({ comment: 'Ma modif' })
  })

  it('bloc Sources : la vue par source, chargée à l’ouverture ; le titre ouvre la fusion, en lecture seule pour un raw (ticket 173c6ed6)', async () => {
    const calls = mockApi({ ok: true, reasons: [], conflicts: [] })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Déplier Sources' }))
    expect(calls.filter((c) => c.path.endsWith('/raw-explorer/changes'))).toHaveLength(1)
    expect(await screen.findByRole('region', { name: 'Source S' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'O.schema.yaml' })).toHaveAttribute('href', expect.stringContaining('/sources?'))
    // Orphelin : hors du brouillon, pas de fusion à ouvrir.
    expect(screen.queryByRole('button', { name: 'ghost.schema.yaml' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Commande' }))
    expect(await screen.findByText(/Objet collecté : sa fusion se consulte sans se modifier/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Valider la fusion' })).not.toBeInTheDocument()
  })

  it('bloc Sources : sans la vue par source (droit absent), l’arbre des fichiers', async () => {
    mockApi({ ok: true, reasons: [], conflicts: [] }, { changesFail: true })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Déplier Sources' }))
    expect(await screen.findByText('O.schema.yaml')).toBeInTheDocument()
    expect(screen.getByText('s/raw/')).toBeInTheDocument()
  })
})
