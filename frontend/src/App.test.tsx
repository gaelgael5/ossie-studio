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

test('espaces : un droit en échec affiche le droit et la cause traduits', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
      },
    },
    '/api/spaces': { body: { spaces: [] } },
    '/api/connections': {
      body: { connections: [{ id: 'c1', label: 'GitHub', forge: 'github', url: 'https://github.com', identity: 'octocat', last_status: 'valid', last_error_code: null, last_checked_at: null }] },
    },
    'POST /api/spaces': {
      status: 400,
      body: { error: 'space_probe_failed', error_args: { right: 'push_commit', cause: 'write_denied', residuals: [] } },
    },
  })
  renderAt('/spaces')
  await screen.findByText('Espaces Git')
  await userEvent.type(screen.getByLabelText("Nom de l'espace"), 'modele')
  await userEvent.type(screen.getByLabelText('URL du dépôt'), 'https://github.com/acme/app')
  await userEvent.click(screen.getByRole('button', { name: 'Éprouver les cinq droits et créer' }))
  // Le message compose le droit et la cause traduits.
  await waitFor(() => expect(screen.getByText(/pousser un commit/)).toBeInTheDocument())
  expect(screen.getByText(/droit d'écriture absent/)).toBeInTheDocument()
})

test('détail espace : liste les branches et propose la création de fille', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
      },
    },
    '/api/spaces/s1/branches': {
      body: {
        branches: [{ id: 'b1', label: 'Principale', slug: 'principale', kind: 'base', git_ref: 'work/principale' }],
        viewed_branch_id: 'b1',
      },
    },
  })
  renderAt('/spaces/s1')
  await screen.findByText('Branches de travail')
  // La base existe → on propose la création d'une fille.
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Créer une branche fille' })).toBeInTheDocument(),
  )
  expect(screen.getAllByText('Principale').length).toBeGreaterThan(0)
})

test('détail espace : liste les commits non publiés et les publie', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
      },
    },
    '/api/spaces/s1/branches': {
      body: {
        branches: [{ id: 'b1', label: 'Principale', slug: 'principale', kind: 'base', git_ref: 'work/principale' }],
        viewed_branch_id: 'b1',
      },
    },
    '/api/spaces/s1/branches/b1/unpublished': {
      body: {
        commits: [
          { sha: 'abc', author: 'Alice', date: '2026-01-02T10:00:00Z', message: 'ossie: ajoute data' },
        ],
        count: 1,
      },
    },
    'POST /api/spaces/s1/branches/b1/publish': { body: { published: 1 } },
  })
  renderAt('/spaces/s1')
  await screen.findByText('Commits non publiés')
  // Le commit en attente apparaît avec son message préfixé.
  await waitFor(() => expect(screen.getByText('ossie: ajoute data')).toBeInTheDocument())
  await userEvent.click(screen.getByRole('button', { name: 'Publier' }))
  await waitFor(() =>
    expect(screen.getByText('1 commit(s) publié(s) sur la forge.')).toBeInTheDocument(),
  )
})

test('détail espace : enregistre une version et liste les versions', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
      },
    },
    '/api/spaces/s1/branches': {
      body: {
        branches: [{ id: 'b1', label: 'Principale', slug: 'principale', kind: 'base', git_ref: 'work/principale' }],
        viewed_branch_id: 'b1',
      },
    },
    '/api/spaces/s1/branches/b1/unpublished': { body: { commits: [], count: 0 } },
    '/api/spaces/s1/branches/b1/versions': {
      body: {
        versions: [
          { number: 1, label: 'Première', commit: 'abcdef1234', author: 'Alice', date: '2026-01-02T10:00:00Z' },
        ],
      },
    },
    'POST /api/spaces/s1/branches/b1/versions': { status: 201, body: { number: 2, commit: 'ff00', already_exists: false } },
  })
  renderAt('/spaces/s1')
  await screen.findByText('Versions')
  // La version existante est listée avec son libellé.
  await waitFor(() => expect(screen.getByText('Première')).toBeInTheDocument())
  await userEvent.type(screen.getByLabelText('Libellé de la version'), 'Deuxième')
  await userEvent.click(screen.getByRole('button', { name: 'Enregistrer une version' }))
  await waitFor(() => expect(screen.getByText('Version 2 enregistrée.')).toBeInTheDocument())
})

test('détail espace : affiche le diagramme réseau ferroviaire', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
      },
    },
    '/api/spaces/s1/branches': {
      body: {
        branches: [{ id: 'b1', label: 'Principale', slug: 'principale', kind: 'base', git_ref: 'work/principale' }],
        viewed_branch_id: null,
      },
    },
    '/api/spaces/s1/railmap?limit=200': {
      body: {
        lines: [
          { ref: 'origin/main', name: 'main', slug: '', kind: 'root', lane: 0, shape: 'circle' },
          { ref: 'work/principale', name: 'principale', slug: 'principale', kind: 'base', lane: 1, shape: 'square' },
        ],
        nodes: [
          { sha: 'h1', short: 'h1', lane: 0, row: 1, kind: 'commit', subject: 'init', author: 'A', date: '2026-01-01T00:00:00Z', is_switch: false, is_merge: false, number: null, label: null, version_author: null },
          { sha: 'h2', short: 'h2', lane: 1, row: 0, kind: 'version', subject: 'jalon', author: 'A', date: '2026-01-02T00:00:00Z', is_switch: true, is_merge: false, number: 1, label: 'Première', version_author: 'A' },
        ],
        edges: [{ child: 'h2', parent: 'h1', from_lane: 1, to_lane: 0, from_row: 0, to_row: 1, kind: 'switch' }],
        total: 2,
        offset: 0,
        limit: 200,
        has_more: false,
      },
    },
  })
  renderAt('/spaces/s1')
  await screen.findByText('Diagramme des versions')
  // La gare (version 1) et la légende de ligne sont rendues.
  await waitFor(() => expect(screen.getAllByText(/Première/).length).toBeGreaterThan(0))
  expect(screen.getByText('principale')).toBeInTheDocument()
  // Le filtre « versions seulement » bascule.
  await userEvent.click(screen.getByRole('button', { name: 'Versions seulement' }))
  expect(screen.getByRole('button', { name: 'Tout afficher' })).toBeInTheDocument()
})
