import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BaseBranchPicker } from './BaseBranchPicker'

const REMOTE = {
  root: 'main',
  branches: [
    { name: 'docs', is_root: false, tracked: false },
    { name: 'main', is_root: true, tracked: false },
    { name: 'work/deja', is_root: false, tracked: true },
  ],
}

function setup() {
  const calls: { method: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, body: init?.body as string | undefined })
      const body = path.endsWith('/branches/remote') ? REMOTE : {}
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  const onDone = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <BaseBranchPicker spaceId="s1" onDone={onDone} onError={() => undefined} />
    </QueryClientProvider>,
  )
  return { calls, onDone }
}

const posted = (calls: { method: string; body?: string }[]) =>
  JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')

afterEach(() => vi.unstubAllGlobals())

describe('BaseBranchPicker', () => {
  it('propose les branches existantes et la racine (marquée), sans celles déjà suivies ni slug', async () => {
    const { calls, onDone } = setup()
    const user = userEvent.setup()
    expect(screen.queryByLabelText(/Slug/)).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Branche existante' }))
    await screen.findByRole('option', { name: 'docs' })
    expect(screen.getByRole('option', { name: 'docs' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'main (racine)' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'work/deja' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'docs' }))
    await user.click(screen.getByRole('button', { name: 'Utiliser cette branche' }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(posted(calls)).toEqual({ git_ref: 'docs' })
  })

  it('crée une nouvelle branche seulement sur demande, par son nom git', async () => {
    const { calls, onDone } = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '+ Créer une nouvelle branche' }))
    const name = screen.getByLabelText('Nom de la nouvelle branche')
    expect(name).toHaveValue('work/')
    await user.type(name, 'documentation')
    await user.click(screen.getByRole('button', { name: 'Créer la branche de base' }))
    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(posted(calls)).toEqual({ new_ref: 'work/documentation' })
  })
})
