import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RawExplorer } from './RawExplorer'

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
  if (path === '') return { path, folders: [{ path: 'src', items: 1, files: 2 }], objects: [], documents: [] }
  if (path === 'src')
    return { path, folders: [{ path: 'src/Domain', items: 2, files: 2 }], objects: [], documents: [] }
  return {
    path,
    folders: [],
    objects: [
      { ...OBJ, file: 'order.json', title: 'Order', badges: ['retained'], visibility: 'public', visibility_source: '', tags: [SERVICE_TAG] },
      {
        ...OBJ,
        file: 'status.json',
        title: 'Status',
        kind: 'enum',
        badges: [],
        visibility: 'internal',
        visibility_source: 'package-private',
      },
    ],
    documents: [],
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
  center: 'app/order.json',
  depth: 1,
  truncated: false,
  nodes: [
    { id: 'app/order.json', slug: 'app', file: 'order.json', title: 'Order', kind: 'class', visibility: 'public', folder: 'src/Domain', description: 'Une commande.', fields: ['id', 'customer'], external: false, level: 0 },
    { id: 'app/customer.json', slug: 'app', file: 'customer.json', title: 'Customer', kind: 'class', visibility: 'public', folder: 'src/Domain/Sub', description: 'Le client.', fields: [], external: false, level: 1 },
    { id: 'app/invoice.json', slug: 'app', file: 'invoice.json', title: 'Invoice', kind: 'class', visibility: '', folder: 'src', description: '', fields: [], external: false, level: -1 },
    { id: '/ext:Money', slug: '', file: '', title: 'Money', kind: '', visibility: '', folder: '', description: '', fields: [], external: true, level: 1 },
  ],
  edges: [
    { from: 'app/order.json', to: 'app/customer.json', field: 'customer', kind: 'field', cardinality: '1', direction: 'outgoing' },
    { from: 'app/order.json', to: '/ext:Money', field: 'money', kind: 'field', cardinality: '0..1', direction: 'outgoing' },
    { from: 'app/invoice.json', to: 'app/order.json', field: 'order', kind: 'field', cardinality: '0..1', direction: 'incoming' },
  ],
}

const DETAIL = {
  file: 'order.json',
  title: 'Order',
  qualified_name: 'app.order',
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
      via: 'app/customer.json',
      via_title: 'Customer',
      via_target: { slug: 'app', file: 'customer.json', title: 'Customer', folder: 'src/Domain/Sub' },
    },
  ],
  description: 'Une commande du client.',
  enum: [],
  fields: [
    { name: 'id', type: { label: 'integer' }, required: true, description: '', annotations: ['[Key]'], exposed: true },
    {
      name: 'customer',
      type: { label: 'Customer', target: { slug: 'app', file: 'customer.json', title: 'Customer', folder: 'src/Domain/Sub' } },
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
      label: 'customer.json',
      target: { slug: 'app', file: 'customer.json', title: 'Customer', folder: 'src/Domain/Sub' },
    },
  ],
  annotations: [],
  origin: { file: 'src/Domain/Order.cs', line: null, checksum: 'abc', parser: 'csharp', branch: 'main', raw_path: 'sources/app/raw/order.json' },
  documents: [{ path: 'docs/guide.md', title: 'Guide' }],
  incoming: [{ field: 'order', kind: 'field', cardinality: '0..1', target: { slug: 'app', file: 'invoice.json', title: 'Invoice', folder: 'src' } }],
  json: '{"title": "Order"}',
  forge_url: 'https://github.com/org/app/blob/main/src/Domain/Order.cs',
}

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
      else if (u.pathname.endsWith('/sources')) body = declared
      else if (u.pathname.endsWith('/raw-explorer')) body = sources
      else if (u.pathname.endsWith('/tree')) body = { ...TREE, mode: u.searchParams.get('mode') }
      else if (u.pathname.endsWith('/folder')) body = folderBody(u.searchParams.get('path') ?? '')
      else if (u.pathname.endsWith('/graph')) body = GRAPH
      else if (u.pathname.endsWith('/document/content'))
        body = { path: 'docs/guide.md', title: 'Guide', markdown: '# Guide\n\n## Commandes\n\nUne Order se crée.' }
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
      else if (u.pathname.endsWith('/object')) body = DETAIL
      else if (u.pathname.endsWith('/document'))
        body = { path: 'docs/guide.md', title: 'Guide', sections: 2, mentions: ['Order'], objects: [] }
      else if (u.pathname.endsWith('/search'))
        body = { results: [{ ...OBJ, file: 'order.json', title: 'Order', badges: [], folder: 'src/Domain' }] }
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

beforeEach(() => localStorage.clear())
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
    expect(screen.getByTestId('loc').textContent).toContain('sel=order.json')
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

  it('la recherche ouvre le dossier de l’objet trouvé, sélectionné', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app')
    await user.type(await screen.findByLabelText('Rechercher un objet dans la source'), 'ord')
    await user.click(await screen.findByRole('button', { name: /Order\s*Domain$/ }))
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('path=src%2FDomain')
    expect(loc).toContain('sel=order.json')
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

  it('pas de filtre sans objet dans le dossier ; l’état ne propose que ceux présents', async () => {
    mockApi()
    renderExplorer('/sources?tab=raw&source=app&path=src')
    await screen.findByRole('button', { name: /^DIR\s*Domain/ })
    expect(screen.queryByLabelText('Nature')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('État')).not.toBeInTheDocument()
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
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.json')
    expect(await screen.findByText('Une commande du client.')).toBeInTheDocument()
    expect(screen.getByText('[Key]')).toBeInTheDocument()
    expect(screen.getAllByText('externe').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'Voir dans la curation' })).toHaveAttribute(
      'href',
      '/sources/app?tab=curation&object=order.json',
    )
    expect(screen.getByRole('link', { name: 'Ouvrir dans la forge' })).toHaveAttribute(
      'href',
      'https://github.com/org/app/blob/main/src/Domain/Order.cs',
    )
    // Un clic sur la référence sélectionne la cible, dans son dossier.
    await user.click(screen.getAllByRole('button', { name: 'Customer' })[0])
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('path=src%2FDomain%2FSub')
    expect(loc).toContain('sel=customer.json')
  })

  it('aperçu : onglets JSON et Historique', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.json')
    await screen.findByText('Une commande du client.')
    await user.click(screen.getByRole('tab', { name: 'JSON' }))
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
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.json')
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
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.json&view=graph')
    expect(await screen.findByText(/références lues dans la source, non validées/)).toBeInTheDocument()
    expect(screen.getByText('customer · 1')).toBeInTheDocument()
    expect(screen.getByText('order · 0..1')).toBeInTheDocument()
    const graph = within(screen.getByRole('img', { name: /Graphe des références/ }))
    await user.hover(graph.getByRole('button', { name: 'Customer' }))
    expect(screen.getByText('Le client.')).toBeInTheDocument()
    await user.click(graph.getByRole('button', { name: 'Customer' }))
    let loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).toContain('sel=customer.json')
    expect(loc).toContain('center=app%2Forder.json') // sélection sans recentrer
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
    expect(screen.getByTestId('loc').textContent).toContain('sel=customer.json')
  })

  it('onglet Technique : le fichier source ouvre la forge dans un nouvel onglet', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.json')
    await screen.findByText('Une commande du client.')
    await user.click(screen.getByRole('tab', { name: /Technique/ }))
    const link = screen.getByRole('link', { name: 'src/Domain/Order.cs' })
    expect(link).toHaveAttribute('href', 'https://github.com/org/app/blob/main/src/Domain/Order.cs')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('graphe : ouvrir un dossier de l’arbre revient à la vue précédente', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.json&view=list')
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
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&sel=order.json&view=graph')
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
    expect(screen.getByTestId('loc').textContent).toContain('open=order.json')
    expect(await screen.findByRole('tab', { name: 'Champs (3)' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Références (2)' })).toBeInTheDocument()
    // Précédent désactivé sur le premier objet du dossier, suivant actif.
    expect(screen.getByRole('button', { name: '‹ Objet précédent' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Objet suivant ›' })).toBeEnabled()
    await user.click(screen.getByRole('tab', { name: 'Documentation (1)' }))
    expect(await screen.findByText(/Cité dans : Commandes/)).toBeInTheDocument()
    expect(document.querySelector('.md mark')?.textContent).toBe('Order')
    await user.click(screen.getByRole('tab', { name: /Historique/ }))
    expect(await screen.findByText('type de total : decimal → Money')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '← Retour aux vignettes' }))
    const loc = screen.getByTestId('loc').textContent ?? ''
    expect(loc).not.toContain('open=')
    expect(loc).toContain('sel=order.json')
    expect(await screen.findByText('Objets')).toBeInTheDocument()
  })

  it('vue exclusive : objet suivant dans l’ordre du dossier', async () => {
    mockApi()
    const user = userEvent.setup()
    renderExplorer('/sources?tab=raw&source=app&path=src%2FDomain&open=order.json&sel=order.json')
    await user.click(await screen.findByRole('button', { name: 'Objet suivant ›' }))
    expect(screen.getByTestId('loc').textContent).toContain('open=status.json')
  })
})
