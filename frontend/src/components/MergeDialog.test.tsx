import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MergeDialog } from './MergeDialog'

const BASE = {
  path: 'sources/s/raw/O.schema.yaml',
  state: 'modified',
  changed_upstream: true,
  readonly: false,
  structured: true,
  theirs_absent: false,
  mine_absent: false,
}

function mockApi(view: unknown) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      const body = method === 'GET' ? view : { path: BASE.path, merged: true }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderDialog(onClose = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MergeDialog spaceId="s1" branchId="b1" path={BASE.path} onClose={onClose} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('MergeDialog', () => {
  it('lecture seule : montre la correspondance, sans bouton de validation', async () => {
    mockApi({ ...BASE, changed_upstream: false, readonly: true, kind: 'none', base: { a: 1 }, theirs: { a: 1 }, mine: { a: 2 }, proposed: null, conflicts: [] })
    renderDialog()
    await waitFor(() => expect(screen.getByText('/a')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Valider la fusion' })).not.toBeInTheDocument()
  })

  it('conflit : la validation attend un choix, puis envoie la saisie libre', async () => {
    const calls = mockApi({
      ...BASE,
      kind: 'conflict',
      base: { a: 1 },
      theirs: { a: 3 },
      mine: { a: 2 },
      proposed: { a: 2 },
      conflicts: [{ pointer: '/a', base: 1, theirs: 3, mine: 2 }],
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderDialog(onClose)
    const validate = await screen.findByRole('button', { name: 'Valider la fusion' })
    expect(validate).toBeDisabled()
    await user.click(screen.getByRole('radio', { name: 'Saisie libre' }))
    await user.type(screen.getByLabelText('Valeur (JSON ou texte)'), '42')
    await user.click(validate)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const post = calls.find((c) => c.method === 'POST')
    expect(JSON.parse(post?.body ?? '{}')).toEqual({ path: BASE.path, resolutions: { '/a': { value: 42 } } })
  })

  it('supprimé côté publié : propose de garder ma version ou d’accepter la suppression', async () => {
    const calls = mockApi({ ...BASE, kind: 'deleted_upstream', base: { a: 1 }, theirs: null, theirs_absent: true, mine: { a: 2 }, proposed: { a: 2 }, conflicts: [] })
    const user = userEvent.setup()
    renderDialog()
    await user.click(await screen.findByRole('button', { name: 'Garder ma version' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')).toEqual({ path: BASE.path, delete: false })
  })
})
