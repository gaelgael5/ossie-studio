import { render, screen, waitFor } from '@testing-library/react'
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
}

function folderBody(path: string) {
  if (path === '') return { path, folders: [{ path: 'src', items: 1, files: 2 }], objects: [], documents: [] }
  if (path === 'src')
    return { path, folders: [{ path: 'src/Domain', items: 2, files: 2 }], objects: [], documents: [] }
  return {
    path,
    folders: [],
    objects: [
      { ...OBJ, file: 'order.json', title: 'Order', badges: ['retained'], visibility: 'public', visibility_source: '' },
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

const DETAIL = {
  file: 'order.json',
  title: 'Order',
  qualified_name: 'app.order',
  kind: 'class',
  badges: ['retained'],
  excluded_by: '',
  description: 'Une commande du client.',
  enum: [],
  fields: [
    { name: 'id', type: { label: 'integer' }, required: true, description: '', annotations: ['[Key]'] },
    {
      name: 'customer',
      type: { label: 'Customer', target: { slug: 'app', file: 'customer.json', title: 'Customer', folder: 'src/Domain/Sub' } },
      required: false,
      description: '',
      annotations: [],
    },
    { name: 'money', type: { label: 'Money', external: true }, required: false, description: '', annotations: [] },
  ],
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
  documents: [],
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
      else if (u.pathname.endsWith('/object/history'))
        body = { commits: [{ sha: 'abcdef123', author: 'gael', date: '2026-11-22', message: 'collecte' }] }
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
})
