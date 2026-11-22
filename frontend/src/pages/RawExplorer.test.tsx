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
      { ...OBJ, file: 'order.json', title: 'Order', badges: ['retained'] },
      { ...OBJ, file: 'status.json', title: 'Status', kind: 'enum', badges: [] },
    ],
    documents: [],
  }
}

function mockApi(sources = SOURCES) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      calls.push(url)
      const u = new URL(url, 'http://test')
      let body: unknown = {}
      if (u.pathname.endsWith('/raw-explorer')) body = sources
      else if (u.pathname.endsWith('/tree')) body = { ...TREE, mode: u.searchParams.get('mode') }
      else if (u.pathname.endsWith('/folder')) body = folderBody(u.searchParams.get('path') ?? '')
      else if (u.pathname.endsWith('/search'))
        body = { results: [{ ...OBJ, file: 'order.json', title: 'Order', badges: [], folder: 'src/Domain' }] }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
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
    await user.click(await screen.findByRole('button', { name: /Order/ }))
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
})
