import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import i18n from './i18n'
import { App } from './App'

interface MockRoutes {
  [url: string]: { status?: number; body?: unknown }
}

function mockFetch(routes: MockRoutes) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = init?.method ?? 'GET'
      const key = `${method} ${url}`
      const match = routes[key] ?? routes[url]
      const status = match?.status ?? 200
      return Promise.resolve({
        ok: status < 400,
        status,
        text: () => Promise.resolve(match?.body ? JSON.stringify(match.body) : ''),
      })
    }),
  )
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  return i18n.changeLanguage('fr')
})

test('base vide : la racine mène à l’écran de création d’administrateur', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': { body: { bootstrap_required: true, authenticated: false, user: null } },
  })
  renderAt('/')
  await waitFor(() =>
    expect(screen.getByText('Bienvenue dans Ossie Studio')).toBeInTheDocument(),
  )
})

test('admin connecté : la racine affiche l’accueil avec le lien secrets', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
      },
    },
  })
  renderAt('/')
  await waitFor(() => expect(screen.getByText('Gérer les secrets')).toBeInTheDocument())
})

test('login : des identifiants faux affichent le message traduit', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': { body: { bootstrap_required: false, authenticated: false, user: null } },
    '/api/auth/connectors': { body: [] },
    'POST /api/login': { status: 401, body: { error: 'invalid_credentials' } },
  })
  renderAt('/login')
  await screen.findByText('Connexion')
  await userEvent.type(screen.getByLabelText('Adresse email'), 'x@example.org')
  await userEvent.type(screen.getByLabelText('Mot de passe'), 'mauvais-mot-de-passe')
  await userEvent.click(screen.getByRole('button', { name: 'Se connecter' }))
  await waitFor(() =>
    expect(screen.getByText('Email ou mot de passe incorrect.')).toBeInTheDocument(),
  )
})

test('forges : un jeton invalide affiche le message traduit', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
      },
    },
    '/api/connections': { body: { connections: [] } },
    'POST /api/connections': { status: 400, body: { error: 'connection_token_invalid' } },
  })
  renderAt('/forges')
  await screen.findByText('Forges et connexions')
  await userEvent.type(screen.getByLabelText('Libellé'), 'GitHub perso')
  await userEvent.type(screen.getByLabelText("Jeton d'accès personnel"), 'mauvais')
  await userEvent.click(screen.getByRole('button', { name: 'Enregistrer' }))
  await waitFor(() =>
    expect(screen.getByText('Jeton invalide ou expiré.')).toBeInTheDocument(),
  )
})
