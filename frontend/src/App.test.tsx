import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import i18n from './i18n'
import { App } from './App'

const ADMIN_CAPS = [
  'secrets:read', 'secrets:write', 'connections:read', 'connections:write',
  'oidc:read', 'oidc:write', 'spaces:read', 'spaces:create', 'spaces:branch',
  'spaces:publish', 'spaces:version', 'governance:read', 'governance:write',
  'governance:certify', 'profiles:read', 'profiles:write', 'users:read', 'users:write',
]

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
        capabilities: ADMIN_CAPS,
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
        capabilities: ADMIN_CAPS,
      },
    },
    '/api/connections': { body: { connections: [] } },
    '/api/connections/forges': {
      body: {
        forges: [
          { forge: 'github', default_url: 'https://github.com', url_required: false, secret_category: 'github' },
          { forge: 'gitlab', default_url: 'https://gitlab.com', url_required: false, secret_category: 'gitlab' },
        ],
      },
    },
    '/api/secrets?category=github': {
      body: {
        secrets: [
          { slug: 'gh-ci', label: 'Jeton CI', category: 'github', updated_at: '2026-01-01T00:00:00Z', used_by: 0 },
        ],
        categories: { github: 'GitHub' },
        category_inputs: { github: 'masked' },
      },
    },
    'POST /api/connections': { status: 400, body: { error: 'connection_token_invalid' } },
  })
  renderAt('/forges')
  await screen.findByText('Forges et connexions')
  // Le choix de la forge se fait sur la page, puis une popup dédiée s'ouvre.
  await userEvent.click(screen.getByRole('button', { name: '+ Ajouter une forge' }))
  await userEvent.click(await screen.findByRole('button', { name: /GitHub — github\.com/ }))
  await userEvent.type(screen.getByLabelText('Libellé'), 'GitHub perso')
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /Jeton CI \(gh-ci\)/ })).toBeInTheDocument(),
  )
  await userEvent.selectOptions(screen.getByLabelText("Jeton d'accès personnel"), 'gh-ci')
  await userEvent.click(screen.getByRole('button', { name: 'Vérifier et enregistrer' }))
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
        capabilities: ADMIN_CAPS,
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
        capabilities: ADMIN_CAPS,
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
        capabilities: ADMIN_CAPS,
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
        capabilities: ADMIN_CAPS,
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
        capabilities: ADMIN_CAPS,
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

test('gouvernance tags : liste, filtre et création', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
        capabilities: ADMIN_CAPS,
      },
    },
    '/api/governance/tags': {
      body: {
        tags: [
          { id: 'gouvernance/pii', owner: 'equipe-data', description: 'PII', links: {}, properties: {}, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
        ],
      },
    },
    'POST /api/governance/tags': {
      status: 201,
      body: { id: 'gouvernance/rgpd', owner: '', description: '', links: {}, properties: {}, created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' },
    },
  })
  renderAt('/governance/tags')
  await screen.findByText('Gouvernance › Tags')
  await waitFor(() => expect(screen.getByText('gouvernance/pii')).toBeInTheDocument())
  // Filtre par préfixe : un préfixe qui ne matche pas vide la liste.
  await userEvent.type(screen.getByLabelText('Filtrer par préfixe'), 'reseau')
  await waitFor(() => expect(screen.getByText('Aucun tag géré.')).toBeInTheDocument())
  // Création : après succès, le champ identifiant se réinitialise.
  await userEvent.clear(screen.getByLabelText('Filtrer par préfixe'))
  await userEvent.type(screen.getByLabelText(/Identifiant/), 'Gouvernance/RGPD')
  await userEvent.click(screen.getByRole('button', { name: 'Créer le tag' }))
  await waitFor(() => expect(screen.getByLabelText(/Identifiant/)).toHaveValue(''))
})

test('gouvernance certifications : liste et presets', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
        capabilities: ADMIN_CAPS,
      },
    },
    '/api/governance/certifications': {
      body: {
        certifications: [
          { id: 'c1', name: 'Or', rank: 1, description: 'top', criteria: '', tag: 'certification/or', icon: '🥇', restricted: false, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
        ],
      },
    },
  })
  renderAt('/governance/certifications')
  await screen.findByText('Gouvernance › Certifications')
  expect(screen.getByRole('button', { name: /niveaux par défaut/ })).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('certification/or')).toBeInTheDocument())
  expect(screen.getByText('🥇 Or')).toBeInTheDocument()
})

test('gouvernance classifications : schéma principal, presets et niveaux', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
        capabilities: ADMIN_CAPS,
      },
    },
    '/api/governance/classifications': {
      body: {
        schemes: [
          {
            id: 'classification',
            name: 'Classification',
            description: '',
            badge_prefix: '',
            multi_value: false,
            is_primary: true,
            display_order: 0,
            levels: [
              { id: 'l1', name: 'Public', label: 'Public', short_name: 'PUB', rank: 1, description: '', criteria: '', color: '', icon: '', position: 0 },
            ],
          },
        ],
      },
    },
  })
  renderAt('/governance/classifications')
  await screen.findByText('Gouvernance › Classifications')
  // Un preset TLP est proposé, et le schéma principal est listé.
  expect(screen.getByRole('button', { name: 'TLP 2.0' })).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('classification')).toBeInTheDocument())
  // Gérer le schéma montre ses niveaux.
  await userEvent.click(screen.getByRole('button', { name: 'Gérer' }))
  await waitFor(() => expect(screen.getAllByText('Public').length).toBeGreaterThan(0))
  expect(screen.getByText('PUB')).toBeInTheDocument()
})

test('profils : grille des capacités, admin verrouillé, création', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
        capabilities: ADMIN_CAPS,
      },
    },
    '/api/profiles': {
      body: {
        profiles: [
          { id: 'adm', name: 'Administrateur', description: '', capabilities: ADMIN_CAPS, is_admin: true, user_count: 1 },
        ],
        catalog: [
          { domain: 'secrets', capabilities: ['secrets:read', 'secrets:write'] },
          { domain: 'spaces', capabilities: ['spaces:read', 'spaces:create', 'spaces:branch', 'spaces:publish', 'spaces:version'] },
        ],
      },
    },
    'POST /api/profiles': {
      status: 201,
      body: { id: 'p1', name: 'Lecteur', description: '', capabilities: ['secrets:read'], is_admin: false, user_count: 0 },
    },
  })
  renderAt('/profiles')
  await screen.findByText('Profils')
  // Le profil administrateur est verrouillé, et la grille montre les domaines.
  await waitFor(() => expect(screen.getByText(/🔒 administrateur/)).toBeInTheDocument())
  expect(screen.getByText('Espaces Git')).toBeInTheDocument()
  expect(screen.getByText('Travailler dans une branche')).toBeInTheDocument()
  // Création : après succès, le champ nom se réinitialise.
  await userEvent.type(screen.getByLabelText('Nom du profil'), 'Lecteur')
  await userEvent.click(screen.getByRole('button', { name: 'Créer le profil' }))
  await waitFor(() => expect(screen.getByLabelText('Nom du profil')).toHaveValue(''))
})

test('utilisateurs : liste, recherche et assignation de profil', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
        capabilities: ADMIN_CAPS,
      },
    },
    '/api/profiles': {
      body: {
        profiles: [
          { id: 'p1', name: 'Lecteur', description: '', capabilities: ['secrets:read'], is_admin: false, is_default: false, user_count: 1 },
        ],
        catalog: [],
      },
    },
    '/api/users?q=&profile_id=&team_id=&limit=50&offset=0': {
      body: {
        users: [
          { id: 'u1', email: 'jose@example.org', display_name: 'José Pérez', is_oidc: true, last_login_at: '2026-01-02T00:00:00Z', profile_id: 'p1', profile_name: 'Lecteur', profile_pinned: false, team_id: 't1', team_name: 'equipe-data', team_origin: 'oidc', team_pinned: false },
        ],
        total: 1,
      },
    },
    '/api/teams': {
      body: {
        teams: [
          { id: 't1', name: 'equipe-data', slug: 'equipe-data', origin: 'oidc' },
          { id: 't2', name: 'Data', slug: 'data', origin: 'manual' },
        ],
      },
    },
    'PUT /api/users/u1/profile': { body: { ok: true } },
  })
  renderAt('/users')
  await screen.findByText('Utilisateurs')
  await waitFor(() => expect(screen.getByText(/José Pérez — jose@example.org/)).toBeInTheDocument())
  expect(screen.getByLabelText('Rechercher (nom ou email)')).toBeInTheDocument()
  // La fiche propose d'assigner un profil (select avec le profil Lecteur).
  expect(screen.getAllByRole('option', { name: 'Lecteur' }).length).toBeGreaterThan(0)
  // Colonne Équipe : filtre, équipe de la personne (venue de l'OIDC) et option de création.
  expect(screen.getByLabelText('Filtrer par équipe')).toBeInTheDocument()
  expect(screen.getAllByRole('option', { name: 'equipe-data' }).length).toBeGreaterThan(0)
  expect(screen.getByRole('option', { name: '+ Créer une équipe…' })).toBeInTheDocument()
})

test('connecteurs OIDC : bloc des correspondances rôle → profil', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
        capabilities: ADMIN_CAPS,
      },
    },
    '/api/oidc/connectors': {
      body: {
        connectors: [
          {
            id: 'c1',
            label: 'IdP',
            issuer: 'http://idp.test',
            accounts: 2,
            client_secret_slug: 'oidc-idp',
            roles_claim: 'realm_access.roles',
            mappings: [{ id: 'm1', role_value: 'ossie-admins', profile_id: 'p1', profile_name: 'Admins' }],
            team_claim: 'department',
            teams: [{ id: 't1', name: 'equipe-data', slug: 'equipe-data' }],
          },
        ],
        default_scopes: 'openid email profile',
        pinned_count: 3,
        callback_url: 'http://test/auth/oidc/callback',
      },
    },
    '/api/oidc/connectors/discovery': {
      body: { c1: 'ok' },
    },
    '/api/secrets?category=oidc': {
      body: {
        secrets: [
          { slug: 'oidc-idp', label: 'Secret IdP', category: 'oidc', updated_at: '2026-01-01T00:00:00Z', used_by: 1 },
        ],
        categories: { oidc: 'OIDC' },
        category_inputs: { oidc: 'masked' },
      },
    },
    '/api/profiles': {
      body: {
        profiles: [
          { id: 'p1', name: 'Admins', description: '', capabilities: [], is_admin: false, is_default: false, user_count: 0 },
        ],
        catalog: [],
      },
    },
  })
  renderAt('/oidc/connectors')
  await screen.findByText('Profil selon les rôles du jeton — IdP')
  await waitFor(() => expect(screen.getByText('ossie-admins')).toBeInTheDocument())
  // Les deux rappels (ordre ; profil fixé à la main avec le nombre de comptes).
  expect(screen.getByText(/première correspondance/)).toBeInTheDocument()
  expect(screen.getByText(/3 compte\(s\)/)).toBeInTheDocument()
  // Bloc Équipe : mention « affichage seulement », chemin du claim, équipes du connecteur.
  await screen.findByText('Équipe — IdP')
  expect(screen.getByText('affichage seulement, ne donne aucun droit')).toBeInTheDocument()
  expect(screen.getByText('equipe-data')).toBeInTheDocument()
  // Client secret par son slug + choix d'un secret oidc dans la liste.
  expect(screen.getByText(/Pour la remplacer/)).toBeInTheDocument()
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /Secret IdP \(oidc-idp\)/ })).toBeInTheDocument(),
  )
  // Badge de découverte + URL de retour à déclarer.
  await waitFor(() => expect(screen.getByText('Découverte OK')).toBeInTheDocument())
  expect(screen.getByText(/URL de retour à déclarer/)).toBeInTheDocument()
  expect(screen.getByText('http://test/auth/oidc/callback')).toBeInTheDocument()
})
