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
  objects: [{ path: 'sources/s/raw/O.json', state: 'modified', changed_upstream: false }],
}

function mockApi(readiness: { ok: boolean; reasons: string[]; conflicts: string[] }) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path.endsWith('/draft')) body = DRAFT
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
    await waitFor(() => expect(screen.getByText('sources/s/raw/O.json')).toBeInTheDocument())
    const button = screen.getByRole('button', { name: 'Publier' })
    expect(button).toBeDisabled() // commentaire vide
    await user.type(screen.getByLabelText('Commentaire du commit'), 'Ma modif')
    await user.click(button)
    await waitFor(() => expect(screen.getByText('Publié : 1 objet(s) en un commit.')).toBeInTheDocument())
    const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/publish'))
    expect(JSON.parse(post?.body ?? '{}')).toEqual({ comment: 'Ma modif' })
  })
})
