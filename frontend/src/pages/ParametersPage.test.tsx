import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ParametersPage } from './ParametersPage'

vi.mock('../hooks', () => ({
  useSession: () => ({ data: { authenticated: true, capabilities: mockCaps.current } }),
}))

const mockCaps: { current: string[] } = { current: ['settings:read', 'settings:write'] }

const DEFAULT = {
  key: 'document_max_bytes',
  value: 204800,
  default: 204800,
  minimum: 1024,
  maximum: 10485760,
  source: 'config',
  updated_at: null,
  updated_by: null,
}

function setup(answers: unknown[]) {
  const calls: { path: string; init?: RequestInit }[] = []
  let i = 0
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      calls.push({ path, init })
      const body = answers[Math.min(i++, answers.length - 1)]
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ParametersPage />
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
  mockCaps.current = ['settings:read', 'settings:write']
})

describe('ParametersPage (ticket ae8fc4b6)', () => {
  it('affiche la taille en Ko, la valeur par défaut, et enregistre en octets', async () => {
    const page = { ...DEFAULT, value: 6144, source: 'page', updated_by: 'admin@example.org' }
    const calls = setup([{ settings: [DEFAULT] }, page, { settings: [page] }])
    const input = await screen.findByLabelText('Taille maximale d’un document écrit par un agent')
    expect(input).toHaveValue('200')
    expect(screen.getByText(/Par défaut : 200 Ko/)).toBeInTheDocument()
    expect(screen.getByText(/valeur de la configuration/)).toBeInTheDocument()
    await userEvent.clear(input)
    await userEvent.type(input, '6')
    await userEvent.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.init?.method === 'PUT')
    expect(put?.path).toBe('/api/settings/document_max_bytes')
    expect(JSON.parse(String(put?.init?.body))).toEqual({ value: 6144 })
    expect(await screen.findByText('Paramètre enregistré.')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Valeur par défaut' })).toBeInTheDocument()
  })

  it('refuse une valeur hors bornes avant tout envoi', async () => {
    setup([{ settings: [DEFAULT] }])
    const input = await screen.findByLabelText('Taille maximale d’un document écrit par un agent')
    await userEvent.clear(input)
    await userEvent.type(input, '0')
    expect(screen.getByText('Un nombre entier entre 1 et 10240 Ko.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
  })

  it('sans settings:write, la valeur se lit sans se modifier', async () => {
    mockCaps.current = ['settings:read']
    setup([{ settings: [DEFAULT] }])
    const input = await screen.findByLabelText('Taille maximale d’un document écrit par un agent')
    expect(input).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).not.toBeInTheDocument()
  })
})
