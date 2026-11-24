import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RawExplorer } from './RawExplorer'
import { setCurrentWorkspace } from '../apiClient'

// BlockNote ne tourne pas dans jsdom : la doublure montre le Markdown reçu.
vi.mock('../components/MarkdownEditor', () => ({
  default: ({ initial, label }: { initial: string; label: string }) => (
    <pre aria-label={`document ${label}`}>{initial}</pre>
  ),
}))

const SOURCES = {
  sources: [
    {
      slug: 'app',
      label: 'Application',
      type: 'git',
      collected: true,
      objects: 2,
      documents: 0,
      last_collect: null,
    },
    {
      slug: 'neuve',
      label: 'Neuve',
      type: 'git',
      collected: false,
      objects: 0,
      documents: 0,
      last_collect: null,
    },
  ],
}

const TREE = {
  ...SOURCES.sources[0],
  mode: 'origin',
  folders: [
    { path: '', items: 1, files: 2 },
    { path: 'src', items: 1, files: 2 },
    { path: 'src/Domain', items: 2, files: 2 },
  ],
}

const OBJ = {
  kind: 'class',
  fields: 3,
  origin: 'src/Domain/Order.cs',
  parser: 'csharp',
  excluded_by: '',
  tags: [] as unknown[],
}

const SERVICE_TAG = { tag: 'technique/service', rule: 'ctrl', rule_name: 'Contrôleurs', reason: 'inherits', value: 'ControllerBase', via: '', via_title: '' }

function folderBody(path: string) {
  // Dossier mixte (tickets 1bd5ae5b, 915c76d2) : un schéma, un contrat, un document.
  if (path === 'mixte')
    return {
      path,
      folders: [],
      objects: [
        { ...OBJ, file: 'order.schema.yaml', title: 'Order', badges: [], visibility: 'public', visibility_source: '' },
        { ...OBJ, file: 'pet.schema.yaml', title: 'Pet', parser: 'openapi', badges: [], visibility: '', visibility_source: '' },
      ],
      documents: [{ path: 'mixte/guide.md', title: 'Guide', sections: 2 }],
      // Ticket e79d008f : la copie intégrale du contrat, nom en dur.
      contracts: [
        { path: 'mixte/contrat.full.openapi.yaml', source: 'mixte/pets.yaml', title: 'API Animaux', version: '1.0', operations: 2 },
      ],
    }
  if (path === '') return { path, folders: [{ path: 'src', items: 1, files: 2 }], objects: [], documents: [] }
  if (path === 'src')
    return { path, folders: [{ path: 'src/Domain', items: 2, files: 2 }], objects: [], documents: [] }
  return {
    path,
    folders: [],
    objects: [
      {
        ...OBJ,
        file: 'order.schema.yaml',
        title: 'Order',
        badges: ['retained'],
        visibility: 'public',
        visibility_source: '',
        tags: [SERVICE_TAG, { ...SERVICE_TAG, tag: 'technique/dto' }, { ...SERVICE_TAG, tag: 'donnees/contact' }],
        referenced_by: 3,
        namespace: 'App.Domain.Orders',
      },
      {
        ...OBJ,
        file: 'status.schema.yaml',
        title: 'Status',
        kind: 'enum',
        badges: STATUS_EXCLUDED.value ? ['excluded'] : STATUS_NEW.value ? ['new'] : [],
        visibility: 'internal',
        visibility_source: 'package-private',
      },
    ],
    documents: [],
  }
}

// Doublure de la recherche combinée : filtre comme le serveur (portée = sources
// demandées, dossier et sous-dossiers ; critères en ET ; facettes de la portée).
type SearchObj = {
  file: string
  title: string
  kind: string
  badges: string[]
  visibility: string
  tags: { tag: string }[]
  source: string
  folder: string
}
function searchBody(u: URL) {
  const sources = u.searchParams.getAll('source')
  const path = u.searchParams.get('path') ?? ''
  const all = [
    ...(folderBody('src/Domain').objects as unknown as SearchObj[]).map((o) => ({
      ...o,
      source: 'app',
      folder: 'src/Domain',
    })),
    { ...OBJ, file: 'client.schema.yaml', title: 'Client', badges: [], visibility: 'public', visibility_source: '', tags: [SERVICE_TAG], source: 'crm', folder: 'Ventes' },
  ] as SearchObj[]
  const scope = all.filter(
    (o) => sources.includes(o.source) && (!path || o.folder === path || o.folder.startsWith(`${path}/`)),
  )
  const q = (u.searchParams.get('q') ?? '').toLowerCase()
  const tag = u.searchParams.get('tag') ?? ''
  const vis = u.searchParams.getAll('visibility')
  const kind = u.searchParams.get('kind') ?? ''
  const badge = u.searchParams.get('badge') ?? ''
  const hide = u.searchParams.get('hide_excluded') === 'true'
  const changed = u.searchParams.get('changed') === 'true'
  const isChanged = (o: SearchObj) => o.badges.includes('new') || o.badges.includes('modified')
  const active = !!(q || tag || vis.length || kind || badge || hide || changed)
  const results = active
    ? scope.filter(
        (o) =>
          (!q || o.title.toLowerCase().includes(q) || o.file.includes(q)) &&
          (!tag || o.tags.some((x) => x.tag === tag)) &&
          (!vis.length || vis.includes(o.visibility)) &&
          (!kind || o.kind === kind) &&
          (!badge || o.badges.includes(badge)) &&
          (!changed || isChanged(o)) &&
          !(hide && o.badges.includes('excluded')),
      )
    : []
  const uniq = (xs: string[]) => [...new Set(xs.filter(Boolean))].sort()
  // Compteurs de l'arbre : toute la source (sans `path`), cumulés vers les parents.
  const tree_counts: Record<string, Record<string, number>> = {}
  if (active) for (const slug of sources) tree_counts[slug] = {}
  if (active)
    for (const o of all.filter((x) => sources.includes(x.source))) {
      const matches =
        (!q || o.title.toLowerCase().includes(q) || o.file.includes(q)) &&
        (!tag || o.tags.some((x) => x.tag === tag)) &&
        (!vis.length || vis.includes(o.visibility)) &&
        (!kind || o.kind === kind) &&
        (!badge || o.badges.includes(badge)) &&
        (!changed || isChanged(o))
      if (!matches) continue
      const counts = (tree_counts[o.source] ??= {})
      const parts = o.folder.split('/')
      for (let i = 0; i <= parts.length; i++) {
        const key = parts.slice(0, i).join('/')
        counts[key] = (counts[key] ?? 0) + 1
      }
    }
  return {
    results,
    tree_counts,
    truncated: false,
    facets: {
      tags: uniq(scope.flatMap((o) => o.tags.map((x) => x.tag))),
      visibilities: uniq(scope.map((o) => o.visibility)),
      kinds: uniq(scope.map((o) => o.kind)),
      badges: uniq(scope.flatMap((o) => o.badges)),
    },
  }
}

const DECLARED = {
  sources: [
    {
      slug: 'app',
      ok: true,
      label: 'Application',
      type: 'git',
      connection: { slug: 'forge', label: 'Forge' },
      parameters: { repository: 'org/app', branch: 'main', parsers: ['csharp'], exclusions: ['tests'] },
    },
  ],
}

const GRAPH = {
  center: 'app/order.schema.yaml',
  depth: 1,
  truncated: false,
  nodes: [
    { id: 'app/order.schema.yaml', slug: 'app', file: 'order.schema.yaml', title: 'Order', kind: 'class', visibility: 'public', folder: 'src/Domain', description: 'Une commande.', fields: ['id', 'customer'], external: false, level: 0 },
    { id: 'app/customer.schema.yaml', slug: 'app', file: 'customer.schema.yaml', title: 'Customer', kind: 'class', visibility: 'public', folder: 'src/Domain/Sub', description: 'Le client.', fields: [], external: false, level: 1 },
    { id: 'app/invoice.schema.yaml', slug: 'app', file: 'invoice.schema.yaml', title: 'Invoice', kind: 'class', visibility: '', folder: 'src', description: '', fields: [], external: false, level: -1 },
    { id: '/ext:Money', slug: '', file: '', title: 'Money', kind: '', visibility: '', folder: '', description: '', fields: [], external: true, level: 1 },
  ],
  edges: [
    { from: 'app/order.schema.yaml', to: 'app/customer.schema.yaml', field: 'customer', kind: 'field', cardinality: '1', direction: 'outgoing' },
    { from: 'app/order.schema.yaml', to: '/ext:Money', field: 'money', kind: 'field', cardinality: '0..1', direction: 'outgoing' },
    { from: 'app/invoice.schema.yaml', to: 'app/order.schema.yaml', field: 'order', kind: 'field', cardinality: '0..1', direction: 'incoming' },
  ],
}

const DETAIL = {
  file: 'order.schema.yaml',
  title: 'Order',
  qualified_name: 'app.order',
  namespace: 'App.Domain',
  kind: 'class',
  badges: ['retained'],
  excluded_by: '',
  tags: [
    SERVICE_TAG,
    {
      ...SERVICE_TAG,
      tag: 'technique/entite',
      reason: '',
      value: '',
      via: 'app/customer.schema.yaml',
      via_title: 'Customer',
      via_target: { slug: 'app', file: 'customer.schema.yaml', title: 'Customer', folder: 'src/Domain/Sub' },
    },
  ],
  removed_tags: [{ ...SERVICE_TAG, tag: 'donnees/personnelles', reason: 'field', value: 'Email' }],
  tag_dependents: { 'technique/service': 2, 'technique/entite': 0 },
  description: 'Une commande du client.',
  enum: [],
  fields: [
    { name: 'id', type: { label: 'integer' }, required: true, description: '', annotations: ['[Key]'], exposed: true },
    {
      name: 'customer',
      type: { label: 'Customer', target: { slug: 'app', file: 'customer.schema.yaml', title: 'Customer', folder: 'src/Domain/Sub' } },
      required: false,
      description: '',
      annotations: [],
      exposed: false,
    },
    { name: 'money', type: { label: 'Money', external: true }, required: false, description: '', annotations: [], exposed: false },
  ],
  exposure: { retained: true, exposed: 1, hidden: 2, missing: ['ancien'] },
  references: [
    {
      field: 'customer',
      kind: 'field',
      label: 'customer.schema.yaml',
      target: { slug: 'app', file: 'customer.schema.yaml', title: 'Customer', folder: 'src/Domain/Sub' },
    },
  ],
  annotations: [],
  origin: { file: 'src/Domain/Order.cs', line: null, checksum: 'abc', parser: 'csharp', branch: 'main', raw_path: 'sources/app/raw/order.schema.yaml' },
  documents: [{ path: 'docs/guide.md', title: 'Guide' }],
  incoming: [
    { field: 'order', kind: 'field', cardinality: '0..1', target: { slug: 'app', file: 'invoice.schema.yaml', title: 'Invoice', folder: 'src' } },
    { field: '', kind: 'inherits', cardinality: '', target: { slug: 'app', file: 'specialorder.schema.yaml', title: 'SpecialOrder', folder: 'src/Domain' } },
    { field: 'order_id', kind: 'foreign_key', cardinality: '1', target: { slug: 'db', file: 'dbo.invoice_line.schema.yaml', title: 'invoice_line', folder: 'erp/dbo' } },
  ],
  json: '{"title": "Order"}',
  forge_url: 'https://github.com/org/app/blob/main/src/Domain/Order.cs',
}

const CONTRACT = {
  path: 'mixte/contrat.full.openapi.yaml',
  source: 'mixte/pets.yaml',
  title: 'API Animaux',
  version: '1.0',
  operations: 2,
  yaml: 'openapi: 3.0.3\ninfo:\n  title: API Animaux\n',
  readable: {
    openapi: '3.0.3',
    title: 'API Animaux',
    version: '1.0',
    description: 'Les animaux de la boutique.',
    servers: ['https://api.exemple.fr'],
    operations: [
      {
        method: 'GET',
        path: '/pets/{id}',
        summary: 'Un animal',
        description: '',
        operation_id: 'getPet',
        tags: [],
        deprecated: false,
        parameters: [{ name: 'id', in: 'path', required: true, description: '', schema: { kind: 'type', type: 'string' } }],
        body: null,
        responses: [
          { code: '200', description: 'l’animal', media: 'application/json', schema: { kind: 'ref', name: 'Pet', file: 'pet.schema.yaml' } },
          { code: '404', description: '' },
        ],
      },
      {
        method: 'DELETE',
        path: '/pets/{id}',
        summary: '',
        description: '',
        operation_id: 'deletePet',
        tags: [],
        deprecated: false,
        parameters: [],
        body: null,
        responses: [],
      },
    ],
    objects: [{ name: 'Pet', file: 'pet.schema.yaml' }],
    schemas: {
      Pet: {
        name: 'Pet',
        file: 'pet.schema.yaml',
        description: 'Un animal de la boutique.',
        shape: { kind: 'object', fields: ['name'] },
        extends: [],
        properties: [
          { name: 'name', required: true, description: 'Le petit nom.', schema: { kind: 'type', type: 'string' } },
        ],
      },
    },
  },
}

const SESSION_CAPS = { value: [] as string[] }
const DESCENDANTS = { value: [] as { slug: string; file: string; title: string; retained: boolean }[] }
const STATUS_EXCLUDED = { value: false }
const STATUS_NEW = { value: false }
const REVIEW = {
  head_sha: 'bbbbbbb',
  objects: [
    {
      path: 'order.schema.yaml',
      title: 'Order',
      status: 'retained-touched',
      fields: [
        { name: 'id', change: 'changed', type: 'string', lost_enrichment: [] },
        { name: 'ancien', change: 'disappeared', type: 'string', lost_enrichment: ['x-businessName'] },
      ],
    },
  ],
}
const DETAIL_OVERRIDE = { value: {} as Record<string, unknown> }

function mockApi(sources = SOURCES, declared: unknown = DECLARED) {
  const calls: string[] = []
  const bodies: { url: string; method: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push(url)
      bodies.push({ url, method: init?.method ?? 'GET', body: init?.body as string | undefined })
      const u = new URL(url, 'http://test')
      let body: unknown = {}
      if (init?.method === 'PUT') body = { slug: 'app', ok: true }
      else if (u.pathname === '/api/session') body = { authenticated: true, capabilities: SESSION_CAPS.value }
      else if (u.pathname.includes('/governance/tag-removals')) body = { ok: true }
      else if (u.pathname.endsWith('/curation/enrichment'))
        body = {
          title: 'Order',
          object: { name: '', business_name: 'Commande', description: '', concept: '', tags: [], readonly: {} },
          fields: [{ name: 'id', business_name: '', description: '', concept: '', tags: [], readonly: {} }],
        }
      else if (u.pathname.endsWith('/curation/review')) body = REVIEW
      else if (u.pathname.endsWith('/curation/refresh')) body = { ok: true, commit: 'abc', refreshed: ['order.schema.yaml'] }
      else if (u.pathname.includes('/curation/')) body = { ok: true, commit: 'abc' }
      else if (u.pathname.endsWith('/sources')) body = declared
      else if (u.pathname.endsWith('/raw-explorer')) body = sources
      else if (u.pathname.endsWith('/tree'))
        body =
          u.searchParams.get('family') === 'markdown'
            ? // Compteurs du seul type demandé : aucun document sous src.
              { ...TREE, mode: u.searchParams.get('mode'), folders: [{ path: '', items: 1, files: 1 }, { path: 'src', items: 1, files: 0 }, { path: 'src/Domain', items: 2, files: 0 }, { path: 'docs', items: 1, files: 1 }] }
            : { ...TREE, mode: u.searchParams.get('mode') }
      else if (u.pathname.endsWith('/folder')) body = folderBody(u.searchParams.get('path') ?? '')
      else if (u.pathname.endsWith('/graph')) body = GRAPH
      else if (u.pathname.endsWith('/incoming')) body = { incoming: DETAIL.incoming }
      else if (u.pathname.endsWith('/descendants')) body = { descendants: DESCENDANTS.value }
      else if (u.pathname.endsWith('/curation/retain-many')) body = { ok: true, commit: 'abc', retained: [] }
      else if (u.pathname.endsWith('/contract')) body = CONTRACT
      else if (u.pathname.endsWith('/document/content'))
        body = { path: 'docs/guide.md', title: 'Guide', markdown: '# Guide\n\n<!-- note interne -->\n\n## Commandes\n\nUne Order se crée.' }
      else if (u.pathname.endsWith('/object/history'))
        body = {
          commits: [
            {
              sha: 'abcdef123',
              author: 'gael',
              date: '2026-11-22',
              message: 'collecte',
              changes: [{ code: 'field_type', field: 'total', before: 'decimal', after: 'Money' }],
            },
          ],
        }
      else if (u.pathname.endsWith('/object')) body = { ...DETAIL, ...DETAIL_OVERRIDE.value }
      else if (u.pathname.endsWith('/document'))
        body = { path: 'docs/guide.md', title: 'Guide', sections: 2, mentions: ['Order'], objects: [] }
      else if (u.pathname.endsWith('/raw-explorer/search')) body = searchBody(u)
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return Object.assign(calls, { bodies })
}

function Location() {
  const loc = useLocation()
  return <output data-testid="loc">{loc.search}</output>
}

function renderExplorer(entry = '/sources?tab=raw') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <RawExplorer spaceId="s1" branchId="b1" spaceName="Espace" />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  SESSION_CAPS.value = []
  DESCENDANTS.value = []
  STATUS_EXCLUDED.value = false
  STATUS_NEW.value = false
  DETAIL_OVERRIDE.value = {}
  setCurrentWorkspace('s1', 'b1')
})
afterEach(() => vi.unstubAllGlobals())

describe('RawExplorer', () => {
  it('ouvre la première source et descend dans les dossiers ; l’adresse suit', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer()
    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('source=app'))
    await user.click(await screen.findByRole('button', { name: /^DIR\s*src/ }))
    await user.click(await screen.findByRole('button', { name: /^DIR\s*Domain/ }))
    expect(await screen.findByText('Order')).toBeInTheDocument()
    expect(screen.getByText('retenu', { selector: '.raw-badge' })).toBeInTheDocument()
    expect(screen.getByText('Objets')).toBeInTheDocument()
    expect(screen.getByTestId('loc').textContent).toContain('path=src%2FDomain')
  })

  it('un clic sélectionne l’objet (dans l’adresse)', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await user.click(await screen.findByRole('button', { name: /^CLS\s*Order/ }))
    expect(screen.getByTestId('loc').textContent).toContain('sel=order.schema.yaml')
    expect(screen.getByRole('button', { name: /^CLS\s*Order/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('le mode choisi est conservé (adresse et rechargement)', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app')
    await user.selectOptions(await screen.findByLabelText('Afficher par'), 'kind')
    await waitFor(() => expect(calls.some((c) => c.includes('mode=kind'))).toBe(true))
    expect(screen.getByTestId('loc').textContent).toContain('mode=kind')
    expect(localStorage.getItem('ossie.rawExplorer.mode')).toBe('kind')
  })

  it('la recherche remplace le contenu par les objets trouvés ; un clic ouvre leur dossier', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app')
    await user.type(await screen.findByLabelText('Rechercher un objet'), 'ord')
    expect(await screen.findByText(/1 objet\(s\) trouvé\(s\)/)).toBeInTheDocument()
    expect(screen.getByTestId('loc').textContent).toContain('q=ord')
    // Les dossiers de la racine ne s'affichent plus : seulement les résultats.
    expect(screen.queryByRole('button', { name: /^DIR\s*src/ })).not.toBeInTheDocument()
    await user.click(screen.getByText('Order'))
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('path=src%2FDomain')
    expect(loc).toContain('sel=order.schema.yaml')
    // Les résultats restent affichés tant que la recherche n'est pas effacée.
    expect(screen.getByText(/1 objet\(s\) trouvé\(s\)/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Effacer les critères' }))
    expect(await screen.findByText('Status')).toBeInTheDocument()
  })

  it('filtre par nature dans le dossier courant', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await screen.findByText('Order')
    await user.selectOptions(screen.getByLabelText('Nature'), 'enum')
    expect(screen.queryByText('Order')).not.toBeInTheDocument()
    expect(screen.getByText('Status')).toBeInTheDocument()
  })

  it('source jamais collectée : message explicite et lien vers la source', async () => {
    mockApi()
    renderExplorer('/sources?tab=raw&source=neuve')
    expect(await screen.findByText(/n’a jamais été collectée/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /collecter/ })).toHaveAttribute('href', '/sources/neuve')
  })

  it('espace sans source : message et lien vers les sources déclarées', async () => {
    mockApi({ sources: [] })
    renderExplorer()
    expect(await screen.findByText('Aucune source déclarée sur cette branche.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Déclarer une source/ })).toHaveAttribute('href', '/sources')
  })

  it('le mode Liste montre un tableau triable', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&view=list')
    await screen.findByText('Order')
    const rows = () => screen.getAllByRole('row').slice(1).map((r) => r.textContent ?? '')
    expect(rows()[0]).toContain('Order')
    await user.click(screen.getByRole('button', { name: 'Nom ▲' }))
    expect(rows()[0]).toContain('Status')
  })

  it('dossier sans objet direct (src) : les filtres proposent ce qu’il y a dessous, combinés', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src')
    await screen.findByRole('button', { name: /^DIR\s*Domain/ })
    const tag = await screen.findByRole('combobox', { name: 'Filtrer par tag de gouvernance' })
    await user.selectOptions(tag, 'technique/service')
    // La recherche est bornée au dossier où elle a été lancée, sous-dossiers compris.
    expect(await screen.findByText('Order')).toBeInTheDocument()
    expect(screen.queryByText('Status')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^DIR\s*Domain/ })).not.toBeInTheDocument()
    expect(screen.getByText('dans src')).toBeInTheDocument()
    let loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('ftag=technique%2Fservice')
    expect(loc).toContain('in=src')
    const last = () => new URL(calls.filter((c) => c.includes('/raw-explorer/search')).at(-1)!, 'http://t')
    expect(last().searchParams.get('path')).toBe('src')
    // Tag ET visibilité : Order est public, pas internal.
    await user.click(screen.getByRole('checkbox', { name: 'internal' }))
    expect(await screen.findByText('Aucun objet trouvé.')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'internal' }))
    expect(await screen.findByText('Order')).toBeInTheDocument()
    // Fermer la puce : toute la source.
    await user.click(screen.getByRole('button', { name: 'Chercher dans toute la source' }))
    await waitFor(() => expect(last().searchParams.get('path')).toBeNull())
    loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).not.toContain('in=')
    // Ouvrir un résultat ne réduit pas la recherche à son dossier.
    await user.click(screen.getByText('Order'))
    expect(screen.getByTestId('loc').textContent).toContain('path=src%2FDomain')
    expect(screen.queryByText('dans Domain')).not.toBeInTheDocument()
    // Effacer : retour à la vue du dossier.
    await user.click(screen.getByRole('button', { name: 'Effacer les critères' }))
    expect(await screen.findByText('Status')).toBeInTheDocument()
  })

  it('l’arbre suit les critères : compteurs mis à jour, dossiers sans objet trouvé masqués', async () => {
    mockApi({
      sources: [SOURCES.sources[0]],
    })
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await screen.findByText('Status')
    const tree = () => within(screen.getByRole('complementary', { name: 'Arbre des sources' }))
    expect(tree().getByRole('button', { name: /Application\s*2/ })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Rechercher un objet'), 'zzz')
    // Aucun objet trouvé : les dossiers disparaissent, la source compte 0.
    await waitFor(() => expect(tree().queryByRole('button', { name: /src/ })).not.toBeInTheDocument())
    expect(tree().getByRole('button', { name: /Application\s*0/ })).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Rechercher un objet'))
    await user.type(screen.getByLabelText('Rechercher un objet'), 'stat')
    expect(await tree().findByRole('button', { name: /Domain\s*1/ })).toBeInTheDocument()
    expect(tree().getByRole('button', { name: /Application\s*1/ })).toBeInTheDocument()
    // Critères effacés : l'arbre redevient complet.
    await user.click(screen.getByRole('button', { name: 'Effacer les critères' }))
    expect(await tree().findByRole('button', { name: /Domain\s*2/ })).toBeInTheDocument()
  })

  it('filtre « Objets modifiés » : absent quand rien n’a changé', async () => {
    mockApi()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await screen.findByText('Status')
    expect(screen.queryByRole('checkbox', { name: 'Objets modifiés' })).not.toBeInTheDocument()
  })

  it('filtre « Objets modifiés » : seuls les nouveaux ou modifiés, dans l’adresse et l’arbre', async () => {
    STATUS_NEW.value = true
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src')
    await user.click(await screen.findByRole('checkbox', { name: 'Objets modifiés' }))
    expect(await screen.findByText('Status')).toBeInTheDocument()
    expect(screen.queryByText('Order')).not.toBeInTheDocument()
    expect(screen.getByTestId('loc').textContent).toContain('fchanged=1')
    const tree = within(screen.getByRole('complementary', { name: 'Arbre des sources' }))
    expect(await tree.findByRole('button', { name: /src\s*1/ })).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Objets modifiés' }))
    expect(await screen.findByRole('button', { name: /^DIR\s*Domain/ })).toBeInTheDocument()
    expect(screen.getByTestId('loc').textContent).not.toContain('fchanged')
  })

  it('filtre Source : plusieurs sources, chaque résultat dit la sienne et ouvre la bonne source', async () => {
    const calls = mockApi({
      sources: [
        ...SOURCES.sources,
        { ...SOURCES.sources[0], slug: 'crm', label: 'CRM' },
      ],
    })
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src')
    await user.click(await screen.findByLabelText('Sources'))
    await user.click(screen.getByRole('checkbox', { name: 'Toutes les sources' }))
    expect(screen.getByTestId('loc').textContent).toContain('fsrc=all')
    // La source seule ne lance pas de recherche ; le tag, si.
    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'Filtrer par tag de gouvernance' }),
      'technique/service',
    )
    expect(await screen.findByText('Client')).toBeInTheDocument()
    expect(screen.getByText('Order')).toBeInTheDocument()
    expect(screen.getByText('dans 2 sources')).toBeInTheDocument()
    const last = new URL(calls.filter((c) => c.includes('/raw-explorer/search')).at(-1)!, 'http://t')
    expect(last.searchParams.getAll('source')).toEqual(['app', 'crm'])
    expect(last.searchParams.get('path')).toBeNull() // un chemin ne vaut pas pour plusieurs sources
    expect(screen.getByText('CRM', { selector: '.raw-tile-source' })).toBeInTheDocument()
    await user.click(screen.getByText('Client'))
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('source=crm')
    expect(loc).toContain('path=Ventes')
    expect(loc).toContain('sel=client.schema.yaml')
  })

  it('les filtres proposés sont ceux du dossier', async () => {
    mockApi()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await screen.findByText('Order')
    const states = screen.getByLabelText('État') as HTMLSelectElement
    expect([...states.options].map((o) => o.textContent)).toEqual(['Tous les états', 'retenu'])
  })

  it('la largeur de l’arbre se règle et se retient', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app')
    const handle = await screen.findByRole('separator', { name: 'Largeur de l’arbre des sources' })
    handle.focus()
    await user.keyboard('{ArrowRight}')
    expect(handle).toHaveAttribute('aria-valuenow', '320')
    expect(localStorage.getItem('ossie.rawExplorer.treeWidth')).toBe('320')
  })

  it('aperçu : description, champs, référence cliquable, type externe, raccourcis', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    expect(await screen.findByText('Une commande du client.')).toBeInTheDocument()
    expect(screen.getByText('[Key]')).toBeInTheDocument()
    expect(screen.getAllByText('externe').length).toBeGreaterThan(0)
    // Sans curation:write, pas de bouton d'asset (lecture seule).
    expect(screen.queryByRole('button', { name: 'Créer un asset' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Ouvrir dans la forge' })).toHaveAttribute(
      'href',
      'https://github.com/org/app/blob/main/src/Domain/Order.cs',
    )
    // Un clic sur la référence sélectionne la cible, dans son dossier.
    await user.click(screen.getAllByRole('button', { name: 'Customer' })[0])
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('path=src%2FDomain%2FSub')
    expect(loc).toContain('sel=customer.schema.yaml')
  })

  it('aperçu : onglets JSON et Historique', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    await user.click(screen.getByRole('tab', { name: 'YAML' }))
    expect(screen.getByText('{"title": "Order"}')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: /Historique/ }))
    expect(await screen.findByText('collecte')).toBeInTheDocument()
  })

  it('aperçu repliable, sans sélection il résume le dossier', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    expect(await screen.findByText(/Sélectionnez une vignette/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Replier l’aperçu' }))
    expect(screen.queryByText(/Sélectionnez une vignette/)).not.toBeInTheDocument()
    expect(localStorage.getItem('ossie.rawExplorer.preview')).toBe('closed')
    await user.click(screen.getByRole('button', { name: '‹ Aperçu' }))
    expect(screen.getByText(/Sélectionnez une vignette/)).toBeInTheDocument()
  })

  it('exclure le dossier courant : confirmation puis écriture du manifeste', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await user.click(await screen.findByRole('button', { name: 'Exclure ce répertoire de la source' }))
    expect(screen.getByText(/Exclure « src\/Domain » de la source/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Exclure' }))
    await waitFor(() => expect(calls.bodies.some((b) => b.method === 'PUT')).toBe(true))
    const put = calls.bodies.find((b) => b.method === 'PUT')
    expect(JSON.parse(put?.body ?? '{}')).toEqual({
      label: 'Application',
      connection: 'forge',
      parameters: {
        repository: 'org/app',
        branch: 'main',
        parsers: ['csharp'],
        exclusions: ['tests', 'src/Domain'],
      },
    })
  })

  it('un dossier déjà exclu est signalé, avec « Réinclure »', async () => {
    mockApi(SOURCES, {
      sources: [{ ...DECLARED.sources[0], parameters: { ...DECLARED.sources[0].parameters, exclusions: ['src'] } }],
    })
    renderExplorer('/sources?tab=raw&source=app&path=src')
    expect(await screen.findByText(/Répertoire exclu par la configuration/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Réinclure' })).toBeInTheDocument()
  })

  it('visibilité : badge avec mot-clé d’origine au survol, filtre à cases', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await screen.findByText('Order')
    const internal = screen.getByText('internal', { selector: '.raw-vis' })
    expect(internal).toHaveAttribute('title', 'package-private')
    expect(screen.getByText('public', { selector: '.raw-vis' })).not.toHaveAttribute('title')
    await user.click(screen.getByRole('checkbox', { name: 'internal' }))
    expect(screen.queryByText('Order')).not.toBeInTheDocument()
    expect(screen.getByText('Status')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'internal' }))
    expect(screen.getByText('Order')).toBeInTheDocument()
  })

  it('œil : exposé / non exposé, légende avec comptes et champs disparus', async () => {
    mockApi()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    expect(screen.getAllByRole('img', { name: 'exposé dans le dataset' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('img', { name: 'non exposé' }).length).toBeGreaterThan(1)
    expect(screen.getByText(/exposé dans le dataset \(1\)/)).toBeInTheDocument()
    expect(screen.getByText(/non exposé \(2\)/)).toBeInTheDocument()
    expect(screen.getByText('1 champ(s) exposé(s) absent(s) du raw')).toBeInTheDocument()
  })

  it('graphe : bandeau raw, liens libellés, clic sélectionne, double-clic recentre', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml&view=graph')
    expect(await screen.findByText(/références lues dans la source, non validées/)).toBeInTheDocument()
    expect(screen.getByText('customer · 1')).toBeInTheDocument()
    expect(screen.getByText('order · 0..1')).toBeInTheDocument()
    const graph = within(screen.getByRole('img', { name: /Graphe des références/ }))
    await user.hover(graph.getByRole('button', { name: 'Customer' }))
    expect(screen.getByText('Le client.')).toBeInTheDocument()
    await user.click(graph.getByRole('button', { name: 'Customer' }))
    let loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('sel=customer.schema.yaml')
    expect(loc).toContain('center=app%2Forder.schema.yaml') // sélection sans recentrer
    // Le tableau des liens : un objet (non externe) se clique aussi.
    const table = within(document.querySelector('.raw-graph-table') as HTMLElement)
    await user.click(table.getByRole('button', { name: 'Invoice' }))
    expect(screen.getByTestId('loc').textContent).toContain('sel=invoice.schema.yaml')
    expect(table.queryByRole('button', { name: 'Money' })).not.toBeInTheDocument()
    await user.dblClick(graph.getByRole('button', { name: 'Customer' }))
    loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).not.toContain('center=')
  })

  it('tags de gouvernance : badge, filtre et provenance dans l’aperçu', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    expect(await screen.findByText('Status')).toBeInTheDocument()
    const badge = screen.getByText('technique/service', { selector: '.raw-tag' })
    expect(badge.getAttribute('title')).toContain('règle « Contrôleurs » : hérite de ControllerBase')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filtrer par tag de gouvernance' }), 'technique/service')
    expect(screen.queryByText('Status')).not.toBeInTheDocument()
    expect(screen.getByText('Order')).toBeInTheDocument()
    await user.click(screen.getByText('Order'))
    expect(await screen.findByText('Tags de gouvernance (posés par règle)')).toBeInTheDocument()
    expect(screen.getByText('règle « Contrôleurs » : hérite de ControllerBase')).toBeInTheDocument()
    const tagsSection = screen.getByText('Tags de gouvernance (posés par règle)').closest('section')!
    await user.click(within(tagsSection).getByRole('button', { name: 'Customer' }))
    expect(screen.getByTestId('loc').textContent).toContain('sel=customer.schema.yaml')
  })

  it('onglet Technique : le fichier source ouvre la forge dans un nouvel onglet', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    await user.click(screen.getByRole('tab', { name: /Technique/ }))
    const link = screen.getByRole('link', { name: 'src/Domain/Order.cs' })
    expect(link).toHaveAttribute('href', 'https://github.com/org/app/blob/main/src/Domain/Order.cs')
    expect(link).toHaveAttribute('target', '_blank')
    expect(screen.getByText('Espace de noms').nextSibling).toHaveTextContent('App.Domain')
    expect(screen.getByText('namespace App.Domain')).toBeInTheDocument()
  })

  it('retirer un tag : confirmation avec les dérivées touchées, puis remettre un tag retiré', async () => {
    SESSION_CAPS.value = ['governance:write']
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    const section = (await screen.findByText('Tags de gouvernance (posés par règle)')).closest('section')!
    expect(within(section).getByText('donnees/personnelles')).toHaveClass('removed')
    const removeButtons = await within(section).findAllByRole('button', { name: 'retirer' })
    await user.click(removeButtons[0])
    expect(
      within(section).getByText(/2 classe\(s\) dérivée\(s\) qui le tiennent de celle-ci le perdront aussi/),
    ).toBeInTheDocument()
    await user.click(within(section).getByRole('button', { name: 'Retirer le tag' }))
    await waitFor(() =>
      expect(calls.bodies.some((b) => b.url.endsWith('/governance/tag-removals') && b.method === 'POST')).toBe(true),
    )
    const sent = calls.bodies.find((b) => b.url.endsWith('/governance/tag-removals'))!
    expect(sent.url).toBe('/api/spaces/s1/branches/b1/governance/tag-removals')
    expect(JSON.parse(sent.body!)).toEqual({ object: 'app/order.schema.yaml', tag: 'technique/service', title: 'Order' })
    await user.click(within(section).getByRole('button', { name: 'remettre' }))
    await waitFor(() => expect(calls.bodies.some((b) => b.url.endsWith('/tag-removals/restore'))).toBe(true))
  })

  it('sans droit de gouvernance : tags en lecture seule', async () => {
    mockApi()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    const section = (await screen.findByText('Tags de gouvernance (posés par règle)')).closest('section')!
    expect(within(section).queryByRole('button', { name: 'retirer' })).not.toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'remettre' })).not.toBeInTheDocument()
  })

  it('asset depuis l’aperçu : créer, puis exposer ou masquer un champ d’un clic sur l’œil', async () => {
    SESSION_CAPS.value = ['curation:write']
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    // L'objet de test est déjà retenu (id exposé) : bouton « Retirer » et œil cliquable.
    await user.click(await screen.findByRole('button', { name: 'Exposer le champ customer' }))
    await waitFor(() => expect(calls.bodies.some((b) => b.url.endsWith('/curation/retain'))).toBe(true))
    const retain = calls.bodies.find((b) => b.url.endsWith('/curation/retain'))!
    expect(JSON.parse(retain.body!)).toEqual({ path: 'order.schema.yaml', fields: ['id', 'customer'] })
    // Lien vers la page Curation de l'asset.
    expect(screen.getByRole('link', { name: 'Ouvrir dans la Curation' })).toHaveAttribute(
      'href',
      '/curation?source=app&path=order.schema.yaml',
    )
    // id est le dernier champ exposé : le masquer retire l'asset, après confirmation.
    await user.click(screen.getByRole('button', { name: 'Masquer le champ id' }))
    const confirm = screen.getByRole('alertdialog', { name: 'Retirer l’asset' })
    expect(confirm).toHaveTextContent('C’est le dernier champ exposé')
    expect(confirm).toHaveTextContent('gouvernance (tags)')
    expect(calls.bodies.filter((b) => b.url.endsWith('/curation/retain')).length).toBe(1)
    await user.click(within(confirm).getByRole('button', { name: 'Annuler' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(calls.bodies.some((b) => b.url.endsWith('/curation/remove'))).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Retirer l’asset' }))
    await user.click(screen.getByRole('button', { name: 'Retirer' }))
    await waitFor(() => expect(calls.bodies.some((b) => b.url.endsWith('/curation/remove'))).toBe(true))
  })

  it('objet non retenu : un clic sur l’œil crée l’asset avec ce seul champ', async () => {
    SESSION_CAPS.value = ['curation:write']
    DETAIL_OVERRIDE.value = { exposure: { retained: false, exposed: 0, hidden: 3, missing: [] } }
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    await user.click(await screen.findByRole('button', { name: 'Exposer le champ customer' }))
    await waitFor(() => expect(calls.bodies.some((b) => b.url.endsWith('/curation/retain'))).toBe(true))
    const sent = calls.bodies.find((b) => b.url.endsWith('/curation/retain'))!
    expect(JSON.parse(sent.body!)).toEqual({ path: 'order.schema.yaml', fields: ['customer'] })
  })

  it('objet non retenu : « Créer un asset » retient tous les champs', async () => {
    SESSION_CAPS.value = ['curation:write']
    DETAIL_OVERRIDE.value = { exposure: { retained: false, exposed: 0, hidden: 3, missing: [] } }
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    await user.click(await screen.findByRole('button', { name: 'Créer un asset' }))
    await waitFor(() => expect(calls.bodies.some((b) => b.url.endsWith('/curation/retain'))).toBe(true))
    const sent = calls.bodies.find((b) => b.url.endsWith('/curation/retain'))!
    expect(JSON.parse(sent.body!)).toEqual({ path: 'order.schema.yaml', fields: null })
  })

  it('onglet Enrichissement : le panneau de la curation, sur l’asset de l’objet', async () => {
    SESSION_CAPS.value = ['curation:write']
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    await user.click(screen.getByRole('tab', { name: 'Enrichissement' }))
    const names = await screen.findAllByRole('textbox', { name: 'Nom métier' })
    expect(names[0]).toHaveValue('Commande')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    expect(await screen.findByText('Enrichissement enregistré dans le brouillon.')).toBeInTheDocument()
    expect(calls.bodies.some((b) => b.url.endsWith('/curation/enrich') && b.method === 'PUT')).toBe(true)
  })

  it('onglet Changements : diff de l’objet par rapport au publié, décisions appliquées dans le brouillon', async () => {
    SESSION_CAPS.value = ['curation:review']
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    expect(await screen.findByRole('tab', { name: 'Changements (2)' })).toBeInTheDocument()
    // « Voir les changements » ouvre cet onglet.
    await user.click(screen.getByRole('button', { name: 'Voir les changements' }))
    expect(screen.getByText('Enrichissements perdus : x-businessName')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: /changé\s*id/ }))
    await user.click(screen.getByRole('button', { name: 'Appliquer les décisions de cet objet' }))
    expect(await screen.findByText('Décisions appliquées dans le brouillon.')).toBeInTheDocument()
    const sent = calls.bodies.find((b) => b.url.endsWith('/curation/review/apply'))!
    expect(JSON.parse(sent.body!)).toEqual({
      head_sha: 'bbbbbbb',
      apply_fields: [{ path: 'order.schema.yaml', name: 'id' }],
      take_fields: [],
      remove_fields: [],
      remove_objects: [],
      retain_objects: [],
    })
  })

  it('« masquer les écartés » cache les objets écartés et se retient dans le navigateur', async () => {
    STATUS_EXCLUDED.value = true
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    expect(await screen.findByText('Status')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'masquer les écartés' }))
    expect(screen.queryByText('Status')).not.toBeInTheDocument()
    expect(screen.getByText('Order')).toBeInTheDocument()
    expect(localStorage.getItem('ossie.rawExplorer.hideExcluded')).toBe('yes')
  })

  it('onglet « Référencé par » : qui référence l’objet, par quel champ, et un clic l’ouvre', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    await user.click(screen.getByRole('tab', { name: 'Référencé par (3)' }))
    const table = within(document.querySelector('.raw-referenced-by') as HTMLElement)
    expect(table.getByText('en hérite')).toBeInTheDocument()
    expect(table.getByText('clé étrangère')).toBeInTheDocument()
    expect(table.getByText('· db')).toBeInTheDocument() // autre source
    expect(table.getByText('order_id')).toBeInTheDocument()
    await user.click(table.getByRole('button', { name: 'Invoice' }))
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('sel=invoice.schema.yaml')
    expect(loc).toContain('path=src')
  })

  it('vignette : compteur « référencé par », menu déroulant, un clic ouvre l’objet ; tags limités', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    const counter = await screen.findByRole('button', { name: 'Référencé par 3 objet(s)' })
    // Au repos, deux tags et « +1 » ; le troisième est dans les détails du survol.
    expect(screen.getByText('+1')).toBeInTheDocument()
    // Détails du survol : le namespace, pas le chemin du fichier.
    expect(screen.getByText('App.Domain.Orders', { selector: '.raw-tile-path' })).toBeInTheDocument()
    expect(calls.some((c) => c.includes('/incoming'))).toBe(false) // chargé à l'ouverture seulement
    await user.click(counter)
    const menu = within(await screen.findByRole('menu'))
    expect(menu.getByText('Référencé par (3)')).toBeInTheDocument()
    expect(menu.getByText('order · champ · 0..1')).toBeInTheDocument()
    expect(menu.getByText('order_id · clé étrangère · 1 · db')).toBeInTheDocument()
    // Le clic sur le compteur n'a pas sélectionné la vignette.
    expect(screen.getByTestId('loc').textContent).not.toContain('sel=')
    await user.click(menu.getByRole('menuitem', { name: /SpecialOrder/ }))
    expect(screen.getByTestId('loc').textContent).toContain('sel=specialorder.schema.yaml')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    // Echap ferme le menu.
    await user.click(screen.getByRole('button', { name: 'Référencé par 3 objet(s)' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('créer un asset avec ses enfants : menu, un commit par source, enfants déjà retenus ignorés', async () => {
    SESSION_CAPS.value = ['curation:write']
    DETAIL_OVERRIDE.value = { exposure: { retained: false, exposed: 0, hidden: 3, missing: [] } }
    DESCENDANTS.value = [
      { slug: 'app', file: 'customer.schema.yaml', title: 'Customer', retained: false },
      { slug: 'app', file: 'address.schema.yaml', title: 'Address', retained: true },
      { slug: 'lib', file: 'money.schema.yaml', title: 'Money', retained: false },
    ]
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml')
    await screen.findByText('Une commande du client.')
    await user.click(await screen.findByRole('button', { name: 'Créer un asset ▾' }))
    const menu = within(screen.getByRole('menu'))
    expect(menu.getByText('Customer, Money')).toBeInTheDocument()
    await user.click(menu.getByRole('menuitem', { name: /Ajouter avec ses enfants \(\+2 objet\(s\)\)/ }))
    await waitFor(() =>
      expect(calls.bodies.filter((b) => b.url.endsWith('/curation/retain-many')).length).toBe(2),
    )
    const sent = calls.bodies
      .filter((b) => b.url.endsWith('/curation/retain-many'))
      .map((b) => [b.url.split('/sources/')[1].split('/')[0], JSON.parse(b.body!).paths])
    expect(sent).toEqual([
      ['app', ['order.schema.yaml', 'customer.schema.yaml']],
      ['lib', ['money.schema.yaml']],
    ])
  })

  it('graphe : ouvrir un dossier de l’arbre revient à la vue précédente', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml&view=list')
    await user.click(await screen.findByRole('button', { name: 'Graphe' }))
    await screen.findByText('order · 0..1')
    const tree = within(screen.getByRole('tree'))
    await user.click(await tree.findByRole('button', { name: /src/ }))
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('view=list')
    expect(loc).not.toContain('back=')
    expect(loc).toContain('path=src')
  })

  it('graphe : les filtres masquent une catégorie de liens sans recharger', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.schema.yaml&view=graph')
    await screen.findByText('order · 0..1')
    const before = calls.filter((c) => c.includes('/graph')).length
    await user.click(screen.getByRole('checkbox', { name: 'entrantes' }))
    expect(screen.queryByText('order · 0..1')).not.toBeInTheDocument()
    expect(calls.filter((c) => c.includes('/graph')).length).toBe(before)
  })

  it('vue exclusive : double-clic, onglets comptés, documentation rendue, historique lisible', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain')
    await user.dblClick(await screen.findByRole('button', { name: /^CLS\s*Order/ }))
    expect(screen.getByTestId('loc').textContent).toContain('open=order.schema.yaml')
    expect(await screen.findByRole('tab', { name: 'Champs (3)' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Références (4)' })).toBeInTheDocument()
    // Précédent désactivé sur le premier objet du dossier, suivant actif.
    expect(screen.getByRole('button', { name: '‹ Objet précédent' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Objet suivant ›' })).toBeEnabled()
    await user.click(screen.getByRole('tab', { name: 'Documentation (1)' }))
    expect(await screen.findByText(/Cité dans : Commandes/)).toBeInTheDocument()
    // Rendu par l'éditeur visuel en lecture seule (ticket 1bd5ae5b), sans les
    // commentaires HTML.
    const shown = await screen.findByLabelText('document Guide')
    expect(shown.textContent).toContain('Une Order se crée.')
    expect(shown.textContent).not.toContain('note interne')
    await user.click(screen.getByRole('tab', { name: /Historique/ }))
    expect(await screen.findByText('type de total : decimal → Money')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '← Retour aux vignettes' }))
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).not.toContain('open=')
    expect(loc).toContain('sel=order.schema.yaml')
    expect(await screen.findByText('Objets')).toBeInTheDocument()
  })

  it('document : un seul clic l’ouvre (ticket 1bd5ae5b)', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=mixte')
    await user.click(await screen.findByRole('button', { name: /^DOC\s*Guide/ }))
    expect(screen.getByTestId('loc').textContent).toContain('open=mixte%2Fguide.md')
  })

  it('filtre Type : schémas, contrats OpenAPI, documents markdown (ticket 915c76d2)', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=mixte')
    await screen.findByRole('button', { name: /^DOC\s*Guide/ })
    const select = screen.getByRole('combobox', { name: 'Type' })
    await user.selectOptions(select, 'openapi')
    expect(screen.getByTestId('loc').textContent).toContain('ffamily=openapi')
    expect(screen.getByText('Pet')).toBeInTheDocument()
    expect(screen.queryByText('Order')).not.toBeInTheDocument()
    expect(screen.queryByText('Guide')).not.toBeInTheDocument()
    await user.selectOptions(select, 'markdown')
    expect(screen.getByText('Guide')).toBeInTheDocument()
    // L'arbre suit le filtre (bug d307159a) : seuls les dossiers qui ont des documents.
    const tree = screen.getByRole('tree')
    await waitFor(() => expect(within(tree).queryByText('Domain')).not.toBeInTheDocument())
    expect(within(tree).getByText('docs')).toBeInTheDocument()
    expect(screen.queryByText('Pet')).not.toBeInTheDocument()
    await user.selectOptions(select, 'schema')
    expect(screen.getByText('Order')).toBeInTheDocument()
    expect(screen.queryByText('Pet')).not.toBeInTheDocument()
    expect(screen.queryByText('Guide')).not.toBeInTheDocument()
  })

  it('contrat OpenAPI complet : vignette au nom en dur, vue lisible, objets ouvrables (ticket e79d008f)', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=mixte')
    expect(await screen.findByText('Contrat OpenAPI complet')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^OAS\s*contrat\.full\.openapi\.yaml/ }))
    expect(screen.getByTestId('loc').textContent).toContain('open=mixte%2Fcontrat.full.openapi.yaml')
    expect(await screen.findByText('Cette API sait faire 2 choses.')).toBeInTheDocument()
    // Un verbe de tous les jours par méthode, et ce qu'on donne / reçoit.
    expect(screen.getAllByText('Lire').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Supprimer').length).toBeGreaterThan(0)
    // La première action est choisie : son détail et son graphe (ticket 82686dc0).
    expect(screen.getAllByText('Ce que tu donnes').length).toBe(1)
    expect(screen.getAllByText('Ça a marché').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Introuvable').length).toBeGreaterThan(0)
    expect(screen.getByRole('img', { name: 'Graphe de l’action /pets/{id}' })).toBeInTheDocument()
    // Clic sur le nœud de la méthode : le bloc du corps remplace la liste des objets (ticket c62f94e9).
    expect(screen.getByText('Les objets de ce contrat')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Voir le corps de cette action' }))
    expect(screen.getByText(/^Le corps de la méthode/)).toBeInTheDocument()
    expect(screen.getByText(/n’envoie pas de paquet/)).toBeInTheDocument()
    expect(screen.queryByText('Les objets de ce contrat')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Revenir à la liste des objets' }))
    expect(screen.getByText('Les objets de ce contrat')).toBeInTheDocument()
    // Un élément du graphe porte son modèle ; un autre le met à jour ; une
    // réponse sans objet efface le bloc.
    await user.click(screen.getByRole('button', { name: 'Ça a marché' }))
    expect(screen.getByText('Le modèle de Ça a marché (200)')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Pet' })[0])
    expect(screen.getByText('Le modèle de Pet')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Introuvable' }))
    expect(screen.queryByText(/^Le modèle de/)).not.toBeInTheDocument()
    expect(screen.getByText('Les objets de ce contrat')).toBeInTheDocument()
    // Clic sur une autre action : son détail remplace le précédent, l'adresse ne change pas.
    const before = screen.getByTestId('loc').textContent
    await user.click(screen.getByRole('button', { name: /Supprimer\s*DELETE/ }))
    expect(screen.getByText('Rien à donner.')).toBeInTheDocument()
    expect(screen.getByTestId('loc').textContent).toBe(before)
    // Le texte complet reste là.
    await user.click(screen.getByRole('tab', { name: 'Texte complet (YAML)' }))
    expect(screen.getByText(/title: API Animaux/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Lecture facile' }))
    // Clic sur un objet : ses propriétés et leurs descriptions à droite.
    await user.click(screen.getAllByRole('button', { name: 'Pet' })[0])
    expect(await screen.findByText('Un animal de la boutique.')).toBeInTheDocument()
    expect(screen.getByText('Le petit nom.')).toBeInTheDocument()
    // L'objet découpé s'ouvre depuis le panneau.
    await user.click(screen.getByRole('button', { name: 'Ouvrir l’objet découpé' }))
    expect(screen.getByTestId('loc').textContent).toContain('open=pet.schema.yaml')
  })

  it('contrat complet : suit le filtre Type', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=mixte')
    await screen.findByText('Contrat OpenAPI complet')
    const select = screen.getByRole('combobox', { name: 'Type' })
    await user.selectOptions(select, 'openapi')
    expect(screen.getByText('Contrat OpenAPI complet')).toBeInTheDocument()
    await user.selectOptions(select, 'schema')
    expect(screen.queryByText('Contrat OpenAPI complet')).not.toBeInTheDocument()
  })

  it('vue exclusive : objet suivant dans l’ordre du dossier', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&open=order.schema.yaml&sel=order.schema.yaml')
    await screen.findByRole('button', { name: 'Objet suivant ›' })
    // Les voisins viennent du contenu du dossier, chargé à part (et la barre est
    // re-rendue une fois l'objet chargé) : on reprend le bouton à jour.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Objet suivant ›' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Objet suivant ›' }))
    expect(screen.getByTestId('loc').textContent).toContain('open=status.schema.yaml')
  })
})
