import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCurrentWorkspace } from '../apiClient'
import { CurationPage } from './CurationPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

// BlockNote ne tourne pas dans jsdom : une doublure garde le contrat de
// l'éditeur (texte initial en Markdown, `getMarkdown` à l'enregistrement).
vi.mock('../components/MarkdownEditor', async () => {
  const { useEffect, useRef } = await import('react')
  return {
    default: function FakeEditor({
      initial,
      editable = true,
      onReady,
      label,
    }: {
      initial: string
      editable?: boolean
      onReady: (h: { getMarkdown: () => string }) => void
      label: string
    }) {
      const ref = useRef<HTMLTextAreaElement>(null)
      useEffect(() => {
        onReady({ getMarkdown: () => ref.current?.value ?? '' })
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [])
      return <textarea ref={ref} aria-label={`éditeur ${label}`} defaultValue={initial} readOnly={!editable} />
    },
  }
})

const BASE = '/api/spaces/s1/branches/b1'

const SUMMARY = {
  source: 'api-boutique',
  path: 'commande.schema.yaml',
  title: 'Commande',
  kind: 'class',
  parser: 'csharp',
  origin: 'Ventes/Commande.cs',
  folder: 'Ventes',
  property_count: 3,
  raw_fields: 4,
  undescribed: 1,
  described: false,
  modified: false,
  orphan: false,
  property_names: ['Client', 'CreeLe', 'Numero', 'numeroCommande'],
}
const CLIENT = { ...SUMMARY, path: 'client.schema.yaml', title: 'Client', undescribed: 0, modified: true, property_names: ['Email'] }
const MERMAID = 'Articles commandés.\n\n```mermaid\ngraph TD; A-->B\n```\n'
const DETAIL = {
  ...SUMMARY,
  description: '',
  business_name: '',
  properties: [
    {
      name: 'Numero',
      business_name: 'numeroCommande',
      type: 'string',
      format: '',
      max_length: 20,
      ref: null,
      external: '',
      description: 'Numéro lisible.',
      missing: false,
    },
    {
      name: 'Client',
      business_name: '',
      type: '',
      format: '',
      max_length: null,
      ref: { source: 'api-boutique', path: 'client.schema.yaml', title: 'Client', kind: 'field', retained: true },
      external: '',
      description: MERMAID,
      missing: false,
    },
    {
      name: 'CreeLe',
      business_name: '',
      type: 'string',
      format: 'date-time',
      max_length: null,
      ref: null,
      external: '',
      description: '',
      missing: false,
    },
  ],
}

function mockApi(capabilities: string[] = ['curation:read', 'curation:write'], assets = [SUMMARY, CLIENT]) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path === `${BASE}/curation-assets`)
        body = { sources: assets.length ? [{ slug: 'api-boutique', label: 'api-boutique' }] : [], assets }
      else if (path.startsWith(`${BASE}/curation-assets/asset/history`)) body = { commits: [] }
      else if (path.startsWith(`${BASE}/curation-assets/asset`)) {
        body = path.includes('client.schema.yaml')
          ? { ...DETAIL, ...CLIENT, properties: [] }
          : DETAIL
      } else if (method === 'PUT' && path.endsWith('/curation/enrich')) body = { ok: true, commit: 'draft:2' }
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify(body)),
        json: () => Promise.resolve(body),
      })
    }),
  )
  return calls
}

function renderPage(url = '/curation') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <CurationPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => setCurrentWorkspace('s1', 'b1'))
afterEach(() => vi.unstubAllGlobals())

describe('CurationPage', () => {
  it('liste les seuls assets retenus, avec compteurs et filtres', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('button', { name: /Commande/ })).toBeInTheDocument()
    expect(screen.getByText('1 à décrire')).toBeInTheDocument()
    expect(screen.getByText('décrit')).toBeInTheDocument()
    expect(screen.getByText(/Seuls les objets retenus apparaissent ici/)).toBeInTheDocument()
    // La recherche porte aussi sur les propriétés (nom raw ou métier).
    await user.type(screen.getByRole('searchbox'), 'numerocom')
    expect(screen.getByRole('button', { name: /Commande/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Client/ })).not.toBeInTheDocument()
    await user.clear(screen.getByRole('searchbox'))
    await user.click(screen.getByRole('button', { name: 'Sans description (1)' }))
    expect(screen.queryByRole('button', { name: /Client/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Modifiés dans raw (1)' }))
    expect(screen.getByRole('button', { name: /Client/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Commande/ })).not.toBeInTheDocument()
  })

  it('aucun asset : renvoie vers l’Explorateur raw', async () => {
    mockApi(undefined, [])
    renderPage()
    expect(await screen.findByText('Aucun asset retenu sur cette branche.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Retenir des objets depuis l’Explorateur raw' })).toBeInTheDocument()
  })

  it('tableau des propriétés : nom métier, nom raw, type, début de description, mermaid', async () => {
    mockApi()
    const user = userEvent.setup()
    renderPage('/curation?source=api-boutique&path=commande.schema.yaml')
    const table = await screen.findByRole('table')
    expect(within(table).getByText('numeroCommande')).toBeInTheDocument()
    expect(within(table).getByText('raw : Numero')).toBeInTheDocument()
    expect(within(table).getByText('string ≤ 20')).toBeInTheDocument()
    expect(within(table).getByText('date-time')).toBeInTheDocument()
    expect(within(table).getByText('mermaid')).toBeInTheDocument()
    expect(within(table).getByText('— à décrire')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Voir dans l’Explorateur raw' })).toHaveAttribute(
      'href',
      '/sources?tab=raw&source=api-boutique&path=Ventes&sel=commande.schema.yaml',
    )
    // Une référence vers un asset retenu le sélectionne.
    await user.click(within(table).getByRole('button', { name: '→ Client' }))
    expect(await screen.findByRole('heading', { name: 'Client' })).toBeInTheDocument()
  })

  it('« Rédiger » : la description existante s’ouvre telle quelle, « Enregistrer » n’envoie qu’elle', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage('/curation?source=api-boutique&path=commande.schema.yaml')
    await user.click(await screen.findByRole('button', { name: 'Rédiger la description de Client' }))
    const dialog = screen.getByRole('dialog')
    const editor = await within(dialog).findByRole('textbox')
    expect(editor).toHaveValue(MERMAID)
    await user.clear(editor)
    await user.type(editor, 'Client qui a passé la commande.')
    await user.click(within(dialog).getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe(`${BASE}/sources/api-boutique/curation/enrich`)
    expect(JSON.parse(put.body!)).toEqual({
      path: 'commande.schema.yaml',
      fields: { Client: { description: 'Client qui a passé la commande.' } },
    })
  })

  it('« Annuler » ferme sans rien écrire', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage('/curation?source=api-boutique&path=commande.schema.yaml')
    await user.click(await screen.findByRole('button', { name: 'Rédiger la description de CreeLe' }))
    const dialog = screen.getByRole('dialog')
    await user.type(await within(dialog).findByRole('textbox'), 'brouillon')
    await user.click(within(dialog).getByRole('button', { name: 'Annuler' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('description de l’asset : même fenêtre, sur l’objet', async () => {
    const calls = mockApi()
    const user = userEvent.setup()
    renderPage('/curation?source=api-boutique&path=commande.schema.yaml&tab=description')
    expect(await screen.findByText('— à décrire')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Rédiger' }))
    const dialog = screen.getByRole('dialog')
    await user.type(await within(dialog).findByRole('textbox'), 'Une commande.')
    await user.click(within(dialog).getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)).toEqual({
      path: 'commande.schema.yaml',
      obj: { description: 'Une commande.' },
    })
  })

  it('sans curation:write : « Rédiger » désactivé', async () => {
    mockApi(['curation:read'])
    renderPage('/curation?source=api-boutique&path=commande.schema.yaml')
    const write = await screen.findByRole('button', { name: 'Rédiger la description de CreeLe' })
    expect(write).toBeDisabled()
  })
})
