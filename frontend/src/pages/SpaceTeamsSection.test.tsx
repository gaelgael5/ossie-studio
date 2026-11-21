import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SpaceTeamsSection } from './SpaceTeamsSection'

type Route = { method: string; path: string; body: unknown }

function mockApi(routes: Route[]) {
  const calls: { method: string; path: string; body: string | undefined }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      const route = routes.find((r) => r.method === method && r.path === path)
      return Promise.resolve({
        ok: true,
        status: route ? 200 : 404,
        text: () => Promise.resolve(JSON.stringify(route?.body ?? {})),
      })
    }),
  )
  return calls
}

function renderSection(canManage: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SpaceTeamsSection spaceId="s1" canManage={canManage} />
    </QueryClientProvider>,
  )
}

const ATTACHED = { teams: [{ team_id: 't1', team_name: 'Data', profile_id: 'p1', profile_name: 'Contributeur' }] }

afterEach(() => vi.unstubAllGlobals())

describe('SpaceTeamsSection', () => {
  it('liste les équipes rattachées, en lecture seule sans le droit fort', async () => {
    mockApi([{ method: 'GET', path: '/api/spaces/s1/teams', body: ATTACHED }])
    renderSection(false)
    await waitFor(() => expect(screen.getByText('Data')).toBeInTheDocument())
    expect(screen.getByText('Contributeur')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rattacher' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retirer' })).not.toBeInTheDocument()
  })

  it('rattache une équipe avec un profil quand on a le droit fort', async () => {
    const calls = mockApi([
      { method: 'GET', path: '/api/spaces/s1/teams', body: { teams: [] } },
      { method: 'GET', path: '/api/teams', body: { teams: [{ id: 't1', name: 'Data' }] } },
      { method: 'GET', path: '/api/profiles', body: { profiles: [{ id: 'p1', name: 'Contributeur' }], catalog: [] } },
      { method: 'PUT', path: '/api/spaces/s1/teams/t1', body: ATTACHED },
    ])
    const user = userEvent.setup()
    renderSection(true)
    await waitFor(() => expect(screen.getByRole('option', { name: 'Data' })).toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('option', { name: 'Contributeur' })).toBeInTheDocument())
    const attach = screen.getByRole('button', { name: 'Rattacher' })
    expect(attach).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Équipe'), 't1')
    await user.selectOptions(screen.getByLabelText('Profil'), 'p1')
    await user.click(attach)
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')
    expect(JSON.parse(put?.body ?? '{}')).toEqual({ profile_id: 'p1' })
  })
})
