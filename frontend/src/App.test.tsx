import { render, screen, waitFor, within } from '@testing-library/react'
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
  'sources:read', 'sources:declare', 'sources:collect', 'parsing:read', 'parsing:manage',
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
    'POST /api/auth/login/analyze': { body: { next: 'password', connectors: [] } },
    'POST /api/login': { status: 401, body: { error: 'invalid_credentials' } },
  })
  renderAt('/login')
  await screen.findByText('Connexion')
  // Connexion en deux temps (ticket 145b1414) : l'email, la flèche, puis le mot de passe.
  await userEvent.type(screen.getByLabelText('Adresse email'), 'x@example.org')
  await userEvent.click(screen.getByRole('button', { name: 'Continuer' }))
  await userEvent.type(await screen.findByLabelText('Mot de passe'), 'mauvais-mot-de-passe')
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
    '/api/db-connections': { body: { connections: [] } },
    '/api/db-connections/engines': {
      body: {
        engines: [
          { engine: 'postgres', secret_category: 'postgres', secret_kind: 'masked', target_field: 'host', fields: [{ name: 'host', kind: 'text', default: '', required: true, options: [] }] },
          { engine: 'snowflake', secret_category: 'snowflake', secret_kind: 'keyfile', target_field: 'account', fields: [{ name: 'account', kind: 'text', default: '', required: true, options: [] }] },
        ],
      },
    },
    '/api/secrets?category=snowflake': {
      body: { secrets: [], categories: { snowflake: 'Snowflake' }, category_inputs: { snowflake: 'keyfile' } },
    },
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
  // Section Bases de données : menu des moteurs, popup Snowflake avec fichier de clé.
  expect(screen.getByText('Bases de données')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '+ Ajouter une base' }))
  await userEvent.click(await screen.findByRole('button', { name: /Snowflake —/ }))
  const sfDialog = await screen.findByRole('dialog', { name: 'Nouvelle connexion Snowflake' })
  await userEvent.click(within(sfDialog).getByRole('button', { name: '+' }))
  expect(within(sfDialog).getByLabelText('Fichier de clé privée')).toBeInTheDocument()
})

test('forges : une connexion se réédite, slug figé, valeurs reprises', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: { bootstrap_required: false, authenticated: true, user: { email: 'g@example.org', role: 'admin' }, capabilities: ADMIN_CAPS },
    },
    '/api/connections': {
      body: {
        connections: [
          {
            id: 'c1', label: 'gitlab gael', slug: 'gitlab-gael', forge: 'gitlab', url: 'https://gitlab.com',
            identity: 'gael', last_status: 'error', last_error_code: 'connection_token_invalid',
            last_checked_at: null, secret_slug: 'gl-old', secret_label: 'Ancien', secret_used_by_others: 0,
            used_by_spaces: 0, used_by_sources: 0,
          },
        ],
      },
    },
    '/api/connections/forges': {
      body: { forges: [{ forge: 'gitlab', default_url: 'https://gitlab.com', url_required: false, secret_category: 'gitlab' }] },
    },
    '/api/secrets?category=gitlab': {
      body: {
        secrets: [
          { slug: 'gl-old', label: 'Ancien', category: 'gitlab', updated_at: '2026-01-01T00:00:00Z', used_by: 1 },
          { slug: 'gl-new', label: 'Nouveau', category: 'gitlab', updated_at: '2026-01-01T00:00:00Z', used_by: 0 },
        ],
        categories: { gitlab: 'GitLab' },
        category_inputs: { gitlab: 'masked' },
      },
    },
    'PUT /api/connections/c1': { body: {} },
    '/api/db-connections': { body: { connections: [] } },
    '/api/db-connections/engines': { body: { engines: [] } },
  })
  renderAt('/forges')
  // Toute la ligne est cliquable.
  await userEvent.click(await screen.findByText('https://gitlab.com'))
  const dialog = await screen.findByRole('dialog', { name: 'Modifier la connexion GitLab' })
  expect(within(dialog).getByLabelText('Libellé')).toHaveValue('gitlab gael')
  expect(within(dialog).getByDisplayValue('gitlab-gael')).toBeDisabled()
  await waitFor(() => expect(within(dialog).getByLabelText("Jeton d'accès personnel")).toHaveValue('gl-old'))
  await userEvent.selectOptions(within(dialog).getByLabelText("Jeton d'accès personnel"), 'gl-new')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Vérifier et enregistrer' }))
  const fetchMock = vi.mocked(fetch)
  await waitFor(() => {
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({
      label: 'gitlab gael',
      url: 'https://gitlab.com',
      secret_slug: 'gl-new',
    })
  })
})

test('espaces : toute la ligne ouvre l’espace, sans bouton « Ouvrir »', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: { bootstrap_required: false, authenticated: true, user: { email: 'g@example.org', role: 'admin' }, capabilities: ADMIN_CAPS },
    },
    '/api/spaces': {
      body: {
        spaces: [
          {
            id: 's1', name: 'test', slug: 'test', connection_id: 'c1', repo_url: 'https://github.com/a/b.git',
            last_probe_status: 'valid', last_probe_at: null, created_at: '2026-01-01T00:00:00Z',
          },
        ],
      },
    },
    '/api/connections': { body: { connections: [] } },
    '/api/spaces/s1/branches': { body: { branches: [], viewed_branch_id: null } },
  })
  renderAt('/spaces')
  const row = await screen.findByRole('row', { name: 'Ouvrir l’espace test' })
  expect(screen.queryByRole('link', { name: 'Ouvrir' })).not.toBeInTheDocument()
  await userEvent.click(within(row).getByText('https://github.com/a/b.git'))
  await screen.findByText('Branches de travail')
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
    '/api/connections/c1/repositories': {
      body: {
        repositories: [
          { full_name: 'acme/app', url: 'https://github.com/acme/app', clone_url: 'https://github.com/acme/app.git' },
          { full_name: 'acme/autre', url: 'https://github.com/acme/autre', clone_url: 'https://github.com/acme/autre.git' },
        ],
        total: 2,
      },
    },
    'POST /api/spaces': {
      status: 400,
      body: { error: 'space_probe_failed', error_args: { right: 'push_commit', cause: 'write_denied', residuals: [] } },
    },
  })
  renderAt('/spaces')
  await screen.findByText('Espaces Git')
  // Le formulaire n'est pas affiché d'office : le bouton en haut à droite l'ouvre.
  expect(screen.queryByLabelText("Nom de l'espace")).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '+ Nouvel espace' }))
  await userEvent.type(screen.getByLabelText("Nom de l'espace"), 'modele')
  // Le dépôt se choisit dans la liste filtrable de la connexion, sans taper d'URL.
  // Liste déroulante : un clic la déplie, quelques lettres filtrent, Entrée choisit.
  const picker = screen.getByRole('button', { name: 'Dépôt' })
  expect(picker).toHaveTextContent('Choisir un dépôt…')
  await userEvent.click(picker)
  await screen.findByRole('option', { name: 'acme/autre' })
  // Liste chargée : plus d'indicateur d'attente.
  expect(picker).toHaveAttribute('aria-busy', 'false')
  await userEvent.type(screen.getByPlaceholderText('Filtrer les dépôts…'), 'app')
  expect(screen.queryByRole('option', { name: 'acme/autre' })).not.toBeInTheDocument()
  await userEvent.keyboard('{Enter}')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  expect(picker).toHaveTextContent('acme/app')
  await userEvent.click(screen.getByRole('button', { name: 'Éprouver les cinq droits et créer' }))
  // Le message compose le droit et la cause traduits.
  await waitFor(() => expect(screen.getByText(/pousser un commit/)).toBeInTheDocument())
  expect(screen.getByText(/droit d'écriture absent/)).toBeInTheDocument()
  // Sans toucher la liste, la connexion affichée (la première) est bien envoyée,
  // avec le slug déduit du nom.
  const post = vi.mocked(fetch).mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
  expect(JSON.parse(String((post?.[1] as RequestInit).body))).toMatchObject({
    name: 'modele',
    slug: 'modele',
    connection_id: 'c1',
    repo_url: 'https://github.com/acme/app.git',
  })
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
  // La base existe → le bouton propose une branche fille, qui ouvre son formulaire.
  await userEvent.click(await screen.findByRole('button', { name: '+ Nouvelle branche' }))
  // Pas de slug : un seul champ, le nom git, pré-rempli « work/ ».
  expect(screen.queryByLabelText(/Slug/)).not.toBeInTheDocument()
  const name = screen.getByLabelText('Nom de la branche')
  expect(name).toHaveValue('work/')
  await userEvent.type(name, 'fonction-a')
  await userEvent.click(screen.getByRole('button', { name: 'Créer une branche fille' }))
  await waitFor(() => {
    const post = vi
      .mocked(fetch)
      .mock.calls.find(([url, init]) => String(url).endsWith('/branches/child') && init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ new_ref: 'work/fonction-a' })
  })
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
    '/api/spaces/s1/branches/b1/draft': {
      body: { count: 1, revision: 1, objects: [{ path: 'sources/data.txt', state: 'modified' }] },
    },
  })
  renderAt('/spaces/s1')
  await screen.findByText('Mon brouillon')
  // Le brouillon se résume par son nombre d'objets ; le détail est dans la vue
  // « Modifications » (ticket aad1d3c6).
  // (La barre du brouillon affiche aussi le compteur et le lien.)
  const section = (await screen.findByText('Mon brouillon')).closest('section')!
  await waitFor(() =>
    expect(within(section).getByText('1 objet(s) modifié(s).')).toBeInTheDocument(),
  )
  expect(within(section).getByRole('link', { name: 'Voir les modifications' })).toHaveAttribute(
    'href',
    '/spaces/s1/branches/b1/changes',
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
    '/api/spaces/s1/branches/b1/draft': { body: { count: 0, revision: 0, objects: [] } },
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
  // Le diagramme vit dans l’onglet « Historique », calculé seulement à son ouverture.
  expect(screen.queryByText('Diagramme des versions')).not.toBeInTheDocument()
  await userEvent.click(await screen.findByRole('tab', { name: 'Historique' }))
  await screen.findByText('Diagramme des versions')
  // La gare (version 1) et la légende de ligne sont rendues.
  await waitFor(() => expect(screen.getAllByText(/Première/).length).toBeGreaterThan(0))
  expect(screen.getByText('principale')).toBeInTheDocument()
  // Le filtre « versions seulement » bascule.
  await userEvent.click(screen.getByRole('button', { name: 'Versions seulement' }))
  expect(screen.getByRole('button', { name: 'Tout afficher' })).toBeInTheDocument()
})

test('gouvernance tags : liste, filtre et création', async () => {
  localStorage.setItem('ossie.space', 's1')
  localStorage.setItem('ossie.branch', 'b1')
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
    '/api/spaces/s1/branches/b1/governance/tags': {
      body: {
        tags: [
          { id: 'gouvernance/pii', owner: 'equipe-data', description: 'PII', links: {}, properties: {}, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
          // Bug 44931826 : tag posé par une règle fournie, pas encore enregistré.
          { id: 'technique/service', owner: '', description: '', links: {}, properties: {}, created_at: '', updated_at: '', stored: false, rules: ['C# — contrôleurs ASP.NET'] },
        ],
      },
    },
    'POST /api/spaces/s1/branches/b1/governance/tags': {
      status: 201,
      body: { id: 'gouvernance/rgpd', owner: '', description: '', links: {}, properties: {}, created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' },
    },
  })
  renderAt('/governance/tags')
  await screen.findByText('Gouvernance › Tags')
  // L'arbre liste le tag géré (feuille « pii » sous le groupe « gouvernance »).
  await waitFor(() => expect(screen.getByText('pii')).toBeInTheDocument())
  // Le tag posé par une règle apparaît aussi, marqué, sans bouton Supprimer.
  await userEvent.click(screen.getByText('service'))
  expect(screen.getByText(/Posé par les règles : C# — contrôleurs ASP.NET/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Supprimer' })).not.toBeInTheDocument()
  // Filtre par préfixe : un préfixe qui ne matche pas vide la liste.
  await userEvent.type(screen.getByLabelText('Filtrer par préfixe'), 'reseau')
  await waitFor(() => expect(screen.getByText('Aucun tag géré.')).toBeInTheDocument())
  await userEvent.clear(screen.getByLabelText('Filtrer par préfixe'))
  // Création : « + Nouveau tag » ouvre le formulaire ; au succès il se referme.
  await userEvent.click(screen.getByRole('button', { name: '+ Nouveau tag' }))
  await userEvent.type(screen.getByLabelText(/Identifiant/), 'Gouvernance/RGPD')
  await userEvent.click(screen.getByRole('button', { name: 'Créer le tag' }))
  await waitFor(() => expect(screen.queryByLabelText(/Identifiant/)).not.toBeInTheDocument())
})

test('gouvernance certifications : liste et presets', async () => {
  localStorage.setItem('ossie.space', 's1')
  localStorage.setItem('ossie.branch', 'b1')
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
    '/api/spaces/s1/branches/b1/governance/certifications': {
      body: {
        certifications: [
          { id: 'c1', name: 'Or', rank: 1, description: 'top', criteria: '', tag: 'certification/or', icon: '🥇', restricted: false, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
        ],
      },
    },
  })
  renderAt('/governance/certifications')
  await screen.findByText('Gouvernance › Certifications')
  // La carte du niveau « Or » est listée (présente sur la carte et le badge d'aperçu).
  await waitFor(() => expect(screen.getAllByText('🥇 Or').length).toBeGreaterThan(0))
  // La sélectionner affiche son tag associé dans le détail.
  await userEvent.click(screen.getAllByText('🥇 Or')[0])
  await waitFor(() => expect(screen.getByText('certification/or')).toBeInTheDocument())
})

test('gouvernance classifications : schéma principal, presets et niveaux', async () => {
  localStorage.setItem('ossie.space', 's1')
  localStorage.setItem('ossie.branch', 'b1')
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
    '/api/spaces/s1/branches/b1/governance/classifications': {
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
  // Le schéma principal « Classification » est listé ; le sélectionner montre ses niveaux.
  const schemeItem = await screen.findByRole('button', { name: /Classification/ })
  await userEvent.click(schemeItem)
  await waitFor(() => expect(screen.getAllByText('Public').length).toBeGreaterThan(0))
  expect(screen.getAllByText('PUB').length).toBeGreaterThan(0)
  // Le menu « Ajouter un schéma » propose les presets, dont TLP 2.0.
  await userEvent.click(screen.getByRole('button', { name: /Ajouter un schéma/ }))
  await waitFor(() => expect(screen.getByRole('button', { name: /TLP 2.0/ })).toBeInTheDocument())
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
  // Création : le bouton ouvre le formulaire (avec la grille), qui se referme après succès.
  await userEvent.click(screen.getByRole('button', { name: '+ Nouveau profil' }))
  expect(screen.getByText('Espaces Git')).toBeInTheDocument()
  expect(screen.getByText('Travailler dans une branche')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Nom du profil'), 'Lecteur')
  await userEvent.click(screen.getByRole('button', { name: 'Créer le profil' }))
  await waitFor(() => expect(screen.queryByLabelText('Nom du profil')).not.toBeInTheDocument())
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
          { id: 'u1', email: 'jose@example.org', display_name: 'José Pérez', is_oidc: true, last_login_at: '2026-01-02T00:00:00Z', profile_id: 'p1', profile_name: 'Lecteur', profile_pinned: false, teams: [{ id: 't1', name: 'equipe-data', origin: 'oidc' }], space_rights: [{ space_id: 's1', space_name: 'Entrepôt', profile_name: 'Contributeur', team_name: 'equipe-data' }] },
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
  expect(screen.getByText('Entrepôt : Contributeur (via equipe-data)')).toBeInTheDocument()
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
            email_domains: ['pickup-services.com'],
            login_prompt: 'Continuez avec Pickup',
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
  // Connexion par domaine (ticket 145b1414) : domaines affichés avec « @ », phrase.
  expect(screen.getByText('Connexion par domaine — IdP')).toBeInTheDocument()
  expect(screen.getByLabelText("Domaines d'email attendus")).toHaveValue('@pickup-services.com')
  expect(screen.getByLabelText('Phrase sur l’écran de connexion')).toHaveValue('Continuez avec Pickup')
  // Client secret par son slug + choix d'un secret oidc dans la liste.
  expect(screen.getByText(/Pour la remplacer/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '+ Nouveau connecteur' }))
  await waitFor(() =>
    expect(screen.getByRole('option', { name: /Secret IdP \(oidc-idp\)/ })).toBeInTheDocument(),
  )
  // Badge de découverte + URL de retour à déclarer.
  await waitFor(() => expect(screen.getByText('Découverte OK')).toBeInTheDocument())
  expect(screen.getByText(/URL de retour à déclarer/)).toBeInTheDocument()
  expect(screen.getByText('http://test/auth/oidc/callback')).toBeInTheDocument()
})

test('sources : liste les sources déclarées de la branche regardée', async () => {
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
    '/api/spaces': {
      body: {
        spaces: [
          {
            id: 'sp1',
            name: 'modele',
            connection_id: 'c1',
            repo_url: '',
            last_probe_status: 'valid',
            last_probe_at: null,
          },
        ],
      },
    },
    '/api/spaces/sp1/branches': {
      body: {
        branches: [
          { id: 'br1', label: 'Principale', slug: 'principale', kind: 'base', git_ref: 'work/principale' },
        ],
        viewed_branch_id: 'br1',
      },
    },
    '/api/spaces/sp1/branches/br1/sources': {
      body: {
        sources: [
          {
            slug: 'api-boutique',
            ok: true,
            label: 'API boutique',
            type: 'git',
            connection: { id: 'c1', label: 'GitHub', kind: 'forge', forge: 'github' },
            parameters: { repository: 'org/api', branch: 'main', directories: ['src'], parsers: ['markdown'] },
          },
        ],
      },
    },
  })
  renderAt('/sources')
  await screen.findByText('Sources déclarées')
  await waitFor(() => expect(screen.getByText('API boutique')).toBeInTheDocument())
  // Ce qui est lu vient des paramètres (clés anglaises du manifeste version 2).
  expect(screen.getByText('org/api @ main · src')).toBeInTheDocument()
  // Toute la ligne ouvre la source ; « Modifier » garde sa propre action (plus bas).
  expect(screen.getByText('API boutique').closest('tr')).toHaveClass('row-clickable')
  // Le sélecteur d'espace de l'en-tête est fonctionnel.
  expect(screen.getByRole('option', { name: 'Espace : modele' })).toBeInTheDocument()
  // L'aperçu du manifeste montre la version 2 et uniquement des clés anglaises.
  await userEvent.click(screen.getByRole('button', { name: 'Modifier' }))
  const dialog = await screen.findByRole('dialog', { name: 'Modifier la source Git' })
  const preview = within(dialog).getByText(/^version: 2/).textContent ?? ''
  expect(preview).toContain('connection: ')
  expect(preview).toContain('parameters:\n  repository: org/api\n  branch: main\n  parsers: [markdown]\n  directories: [src]')
  expect(preview).not.toMatch(/connexion|parametres|depot|branche|parseurs|repertoires/)
})

test('détail source : collecte une source Git et affiche le rapport', async () => {
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
    '/api/spaces': {
      body: {
        spaces: [
          { id: 'sp1', name: 'modele', connection_id: 'c1', repo_url: '', last_probe_status: 'valid', last_probe_at: null },
        ],
      },
    },
    '/api/spaces/sp1/branches': {
      body: {
        branches: [
          { id: 'br1', label: 'Principale', slug: 'principale', kind: 'base', git_ref: 'work/principale' },
        ],
        viewed_branch_id: 'br1',
      },
    },
    '/api/spaces/sp1/branches/br1/sources': {
      body: {
        sources: [
          {
            slug: 'api-boutique',
            ok: true,
            label: 'API boutique',
            type: 'git',
            connection: { slug: 'github', label: 'GitHub', kind: 'forge', forge: 'github' },
            parameters: { repository: 'org/api', branch: 'main' },
          },
        ],
      },
    },
    '/api/spaces/sp1/branches/br1/sources/api-boutique/collections': {
      body: {
        collections: [
          { sha: '1234567abc', author: 'Alice', date: '2026-01-02T10:00:00Z', message: 'ossie: collecte api-boutique' },
        ],
      },
    },
    'POST /api/spaces/sp1/branches/br1/sources/api-boutique/collect?force=false': {
      body: {
        source_commit: 'abcdef123456',
        local_commit: 'fedcba654321',
        added: 2,
        modified: 1,
        deleted: 0,
        unchanged: 3,
        read: 3,
        retained: 6,
        duration_ms: 42,
        errors: [],
      },
    },
  })
  renderAt('/sources/api-boutique')
  // L'en-tête nomme la source ; l'onglet Collectes est actif par défaut.
  await screen.findByText('API boutique')
  // L'historique vient de Git : les commits publiés qui ont touché la source.
  await waitFor(() => expect(screen.getByText('ossie: collecte api-boutique')).toBeInTheDocument())
  // Lancer la collecte affiche le rapport (étapes + commit source).
  await userEvent.click(screen.getByRole('button', { name: 'Collecter' }))
  await waitFor(() => expect(screen.getByText('Dernière collecte')).toBeInTheDocument())
  expect(screen.getByText(/Code source au commit abcdef1/)).toBeInTheDocument()
  // La collecte est allée dans le brouillon → un lien mène à l'écran de publication.
  expect(screen.getByRole('link', { name: 'Publier la branche' })).toHaveAttribute(
    'href',
    '/spaces/sp1/branches/br1/publish',
  )
})

test('détail source OpenAPI : dépôt d’un contrat, analyse avant enregistrement', async () => {
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
    '/api/spaces': {
      body: {
        spaces: [
          { id: 'sp1', name: 'modele', connection_id: 'c1', repo_url: '', last_probe_status: 'valid', last_probe_at: null },
        ],
      },
    },
    '/api/spaces/sp1/branches': {
      body: {
        branches: [
          { id: 'br1', label: 'Principale', slug: 'principale', kind: 'base', git_ref: 'work/principale' },
        ],
        viewed_branch_id: 'br1',
      },
    },
    '/api/spaces/sp1/branches/br1/sources': {
      body: {
        sources: [
          {
            slug: 'api-paiement',
            ok: true,
            label: 'API paiement',
            type: 'openapi',
            connection: { kind: 'none' },
            parameters: {},
          },
        ],
      },
    },
    '/api/spaces/sp1/branches/br1/sources/api-paiement/contracts': { body: { contracts: [] } },
    '/api/spaces/sp1/branches/br1/sources/api-paiement/collections': { body: { collections: [] } },
    'POST /api/spaces/sp1/branches/br1/sources/api-paiement/contracts/analyze': {
      body: {
        openapi: '3.1.0',
        title: 'API paiement',
        declared_version: '2.0.0',
        schema_count: 2,
        operation_count: 1,
        external_refs: [],
        proposed_version: 'v2',
      },
    },
  })
  const { container } = renderAt('/sources/api-paiement')
  // L'onglet Contrats est actif par défaut pour une source OpenAPI.
  await screen.findByText('Contrats déposés')
  await waitFor(() => expect(screen.getByText('Aucun contrat déposé.')).toBeInTheDocument())
  expect(screen.getByText('Déposer un contrat')).toBeInTheDocument()
  // Déposer un fichier : il est analysé avant l'enregistrement.
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['openapi: 3.1.0'], 'paiement.yaml', { type: 'application/yaml' })
  await userEvent.upload(input, file)
  // L'analyse s'affiche : le nom de version est proposé depuis info.version (v2).
  await waitFor(() =>
    expect(screen.getByLabelText('Enregistrer comme version')).toHaveValue('v2'),
  )
  expect(screen.getByText(/version déclarée 2\.0\.0/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Déposer' })).toBeInTheDocument()
})

test('plugins de parsing : liste et réglage d’un paramètre', async () => {
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
    '/api/parsing/plugins': {
      body: {
        plugins: [
          {
            id: 'markdown',
            label: 'Documents Markdown',
            version: '1.0',
            cible: 'raw',
            globs: ['**/*.md'],
            parametres: [
              {
                slug: 'taille_max_ko',
                label: 'Taille maximale d’un document (Ko)',
                doc: 'Au-delà, le document est signalé et non copié.',
                type: 'int',
                default: '512',
                value: '512',
              },
            ],
          },
        ],
      },
    },
    'PUT /api/parsing/plugins/markdown/parameters/taille_max_ko': {
      body: { plugin_id: 'markdown', slug: 'taille_max_ko', value: '256' },
    },
  })
  renderAt('/parsing')
  await screen.findByText('Documents Markdown')
  const input = screen.getByDisplayValue('512')
  await userEvent.clear(input)
  await userEvent.type(input, '256')
  await userEvent.click(screen.getByRole('button', { name: 'Enregistrer' }))
  await waitFor(() => expect(screen.getByText('Paramètre enregistré.')).toBeInTheDocument())
})

test('menu : « Référentiels » juste après « Espaces » (epic Référentiels)', async () => {
  await i18n.changeLanguage('fr')
  mockFetch({
    '/api/session': {
      body: {
        bootstrap_required: false,
        authenticated: true,
        user: { email: 'gael@example.org', role: 'admin' },
        capabilities: [...ADMIN_CAPS, 'referentiels:read', 'curation:read'],
      },
    },
  })
  renderAt('/')
  const nav = await screen.findByRole('navigation', { name: 'Ossie Studio' })
  await waitFor(() => expect(within(nav).getByRole('link', { name: 'Référentiels' })).toBeInTheDocument())
  const labels = within(nav)
    .getAllByRole('link')
    .map((a) => a.textContent ?? '')
    .filter((text) => ['Espaces', 'Référentiels', 'Sources', 'Curation', 'Gouvernance'].includes(text))
  expect(labels.slice(0, 3)).toEqual(['Espaces', 'Référentiels', 'Sources'])
  expect(labels).toContain('Gouvernance')
})
