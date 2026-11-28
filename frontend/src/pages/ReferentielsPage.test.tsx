import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { ReferentielsPage } from './ReferentielsPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))

// L'éditeur de Miles (JSONJoy + Monaco) est remplacé par un double : un champ pour
// le YAML et un bouton qui simule une modification faite dans l'éditeur visuel.
const visualChange = { current: (m: Record<string, unknown>) => m }
vi.mock('../referentiels/SchemaEditor', () => ({
  default: ({
    text,
    parsed,
    readOnly,
    onVisual,
    onYaml,
  }: {
    text: string
    parsed: { ok: boolean; model?: Record<string, unknown>; message?: string }
    readOnly: boolean
    onVisual: (s: Record<string, unknown>) => void
    onYaml: (t: string) => void
  }) => (
    <div>
      <textarea aria-label="YAML" value={text} readOnly={readOnly} onChange={(e) => onYaml(e.target.value)} />
      {parsed.ok ? (
        <button type="button" onClick={() => onVisual(visualChange.current(parsed.model!))}>
          visuel
        </button>
      ) : (
        <p>illisible</p>
      )}
    </div>
  ),
}))

const BASE = '/api/spaces/s1/branches/b1/referentiels'
const MODEL = {
  title: 'Délais',
  type: 'object',
  'x-key': ['pays', 'mode'],
  required: ['pays', 'mode', 'libelle'],
  properties: {
    pays: { type: 'string' },
    mode: { type: 'string' },
    libelle: { type: 'string' },
    paliers: {
      type: 'array',
      items: { type: 'object', properties: { poids_max: { type: 'number' }, delai_jours: { type: 'integer' } } },
    },
  },
}
const DETAIL = {
  name: 'delais',
  title: 'Délais',
  model: MODEL,
  model_yaml: stringify(MODEL),
  model_path: 'referentiels/delais/modele.schema.yaml',
  count: 412,
  unpublished: false,
  readonly: false,
}
const PREVIEW = {
  ok: true,
  model_problems: [],
  values: 412,
  valid: 412,
  removed: [{ property: 'libelle', values: 37 }],
  problems: [],
  problem_count: 0,
  duplicates: [],
}

function setup(url: string, options: { readonly?: boolean; capabilities?: string[] } = {}) {
  const calls: { method: string; path: string; body?: string }[] = []
  const capabilities = options.capabilities ?? ['referentiels:read', 'referentiels:write']
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path === BASE && method === 'GET')
        body = {
          readonly: options.readonly ?? false,
          referentiels: [
            { name: 'delais', title: 'Délais', key: ['pays', 'mode'], tags: ['entreprise'], count: 412, unpublished: true },
            { name: 'tarifs', title: 'Tarifs', key: ['code'], tags: ['contrainte'], count: 3 },
          ],
        }
      else if (path === BASE) body = { ...DETAIL, name: 'tarifs', title: 'Tarifs' }
      else if (path === `${BASE}/delais/modele/apercu`) body = PREVIEW
      else if (path === `${BASE}/delais/modele`) body = { ...DETAIL, model: JSON.parse(String(init?.body)).model }
      else if (path === `${BASE}/delais/valeurs`) body = { values: [{ pays: 'FR', mode: 'site' }], count: 1 }
      else if (path === `${BASE}/delais/valeurs/verification`)
        body = { ok: false, problems: [{ path: 'FR+site \u203a libelle', code: 'value_required', detail: '', index: 0, field: 'libelle' }] }
      else if (path === `${BASE}/delais/historique`)
        body = { commits: [{ sha: 'abcdef1234567890', author: 'Gael', date: '2026-11-24T08:00:00+00:00', message: 'référentiel : crée delais' }] }
      else if (path === `${BASE}/delais`) body = { ...DETAIL, readonly: options.readonly ?? false }
      else if (path === `${BASE}/tarifs`) body = { ...DETAIL, name: 'tarifs', title: 'Tarifs' }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/referentiels" element={<ReferentielsPage />} />
          <Route path="/referentiels/:name" element={<ReferentielsPage />} />
          <Route path="/referentiels/:name/parametrage" element={<ReferentielsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
  visualChange.current = (m) => m
})

describe('Référentiels — page et onglet Modèle (ticket 829a86a7)', () => {
  it('liste, puis création avec libellé et slug', async () => {
    const calls = setup('/referentiels')
    expect(await screen.findByText('Délais')).toBeInTheDocument()
    expect(screen.getByText('412 valeurs')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '+ Nouveau référentiel' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('Libellé'), 'Tarifs postaux')
    expect(within(dialog).getByLabelText('Slug')).toHaveValue('tarifs-postaux')
    await user.click(within(dialog).getByRole('button', { name: 'Créer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === BASE)).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST' && c.path === BASE)!.body!)).toEqual({
      label: 'Tarifs postaux',
      slug: 'tarifs-postaux',
    })
  })

  it('bandeau de la clé, réordonnée, puis enregistrement', async () => {
    const calls = setup('/referentiels/delais/parametrage')
    expect(await screen.findByText('referentiels/delais/modele.schema.yaml', { exact: false })).toBeInTheDocument()
    const bar = screen.getByRole('region', { name: 'Clé' })
    expect(within(bar).getAllByText('CLÉ')).toHaveLength(2)
    const user = userEvent.setup()
    await user.click(within(bar).getByRole('button', { name: 'Descendre pays' }))
    expect((screen.getByLabelText('YAML') as HTMLTextAreaElement).value).toMatch(/x-key:\n {2}- mode\n {2}- pays/)
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const sent = JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)
    expect(sent.model['x-key']).toEqual(['mode', 'pays'])
  })

  it('une propriété de la clé ne se supprime pas depuis l’éditeur visuel', async () => {
    setup('/referentiels/delais/parametrage')
    await screen.findByText('referentiels/delais/modele.schema.yaml', { exact: false })
    visualChange.current = (m) => {
      const properties = { ...(m.properties as Record<string, unknown>) }
      delete properties.mode
      return { ...m, properties }
    }
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'visuel' }))
    expect(screen.getByRole('alert')).toHaveTextContent('« mode » est dans la clé')
    // Le modèle n'a pas bougé : rien à enregistrer.
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
  })

  it('aperçu de l’effet sur les valeurs avant d’enregistrer', async () => {
    const calls = setup('/referentiels/delais/parametrage')
    await screen.findByText('referentiels/delais/modele.schema.yaml', { exact: false })
    visualChange.current = (m) => {
      const properties = { ...(m.properties as Record<string, unknown>) }
      delete properties.libelle
      return { ...m, properties, required: ['pays', 'mode'] }
    }
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'visuel' }))
    expect(await screen.findByText('Retirer « libelle » la retirerait de 37 valeur(s).')).toBeInTheDocument()
    expect(calls.some((c) => c.path === `${BASE}/delais/modele/apercu`)).toBe(true)
  })

  it('YAML illisible : signalé, saisie gardée, enregistrement bloqué', async () => {
    setup('/referentiels/delais/parametrage')
    await screen.findByText('referentiels/delais/modele.schema.yaml', { exact: false })
    const yaml = screen.getByLabelText('YAML') as HTMLTextAreaElement
    const user = userEvent.setup()
    await user.clear(yaml)
    await user.type(yaml, 'type: object[[properties: [[')
    expect(screen.getByText('illisible')).toBeInTheDocument()
    expect(yaml.value).toBe('type: object[properties: [')
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
  })

  it('lecture seule : ni création ni enregistrement', async () => {
    setup('/referentiels/delais/parametrage', { readonly: true })
    expect(await screen.findByText('lecture seule')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ Nouveau référentiel' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enregistrer' })).not.toBeInTheDocument()
  })

  it('sans le droit de modifier : consultation seule', async () => {
    setup('/referentiels/delais/parametrage', { capabilities: ['referentiels:read'] })
    await screen.findByText('referentiels/delais/modele.schema.yaml', { exact: false })
    expect(screen.queryByRole('button', { name: '+ Nouveau référentiel' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Modifier la clé' })).not.toBeInTheDocument()
  })

  it('onglet Historique : les commits publiés', async () => {
    setup('/referentiels/delais/parametrage')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: 'Historique' }))
    expect(await screen.findByText('référentiel : crée delais')).toBeInTheDocument()
    expect(screen.getByText('abcdef123456')).toBeInTheDocument()
  })

  it('valeurs à l’ouverture : compteur d’erreurs, aucun onglet de paramétrage', async () => {
    setup('/referentiels/delais')
    expect(await screen.findByText('1 erreur')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Modèle' })).not.toBeInTheDocument()
  })
})

describe('Référentiels — liste filtrable, valeurs et paramétrage (ticket aa65d9aa)', () => {
  it('le filtre garde les référentiels dont le libellé ou le nom correspond, sans casse ni accents', async () => {
    setup('/referentiels')
    await screen.findByText('Délais')
    const user = userEvent.setup()
    await user.type(screen.getByRole('searchbox', { name: 'Filtrer les référentiels' }), 'DELAI')
    expect(screen.getByText('Délais')).toBeInTheDocument()
    expect(screen.queryByText('Tarifs')).not.toBeInTheDocument()
    await user.clear(screen.getByRole('searchbox'))
    await user.type(screen.getByRole('searchbox'), 'zzz')
    expect(screen.getByText('Aucun référentiel ne correspond.')).toBeInTheDocument()
  })

  it('le crayon ouvre le paramétrage, « ← Valeurs » revient aux valeurs', async () => {
    setup('/referentiels/delais')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('link', { name: 'Paramétrer Délais' }))
    expect(await screen.findByRole('tab', { name: 'Modèle' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: '← Valeurs' }))
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Modèle' })).not.toBeInTheDocument())
  })

  it('sans le droit de modifier : pas de crayon', async () => {
    setup('/referentiels/delais', { capabilities: ['referentiels:read'] })
    await screen.findByText('Délais')
    expect(screen.queryByRole('link', { name: 'Paramétrer Délais' })).not.toBeInTheDocument()
  })
})

describe('Référentiels — aller sur Imports avec un modèle non enregistré (bug 9cf80cdb)', () => {
  const modify = async () => {
    expect(await screen.findByText('referentiels/delais/modele.schema.yaml', { exact: false })).toBeInTheDocument()
    await userEvent.setup().click(within(screen.getByRole('region', { name: 'Clé' })).getByRole('button', { name: 'Descendre pays' }))
  }

  it('la fenêtre le demande ; Rester garde l’onglet Modèle et la modification', async () => {
    setup('/referentiels/delais/parametrage')
    await modify()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: /Imports/ }))
    expect(screen.getByRole('dialog', { name: 'Modifications non enregistrées' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Rester' }))
    expect(screen.getByRole('tab', { name: /Modèle/ })).toHaveAttribute('aria-selected', 'true')
    expect((screen.getByLabelText('YAML') as HTMLTextAreaElement).value).toMatch(/x-key:\n {2}- mode\n {2}- pays/)
  })

  it('Enregistrer et continuer : le modèle est enregistré, puis Imports s’ouvre', async () => {
    const calls = setup('/referentiels/delais/parametrage')
    await modify()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: /Imports/ }))
    await user.click(screen.getByRole('button', { name: 'Enregistrer et continuer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    await waitFor(() => expect(screen.getByRole('tab', { name: /Imports/ })).toHaveAttribute('aria-selected', 'true'))
  })

  it('Continuer sans enregistrer : le modèle revient à la version enregistrée', async () => {
    const calls = setup('/referentiels/delais/parametrage')
    await modify()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: /Imports/ }))
    await user.click(screen.getByRole('button', { name: 'Continuer sans enregistrer' }))
    expect(screen.getByRole('tab', { name: /Imports/ })).toHaveAttribute('aria-selected', 'true')
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
    await user.click(screen.getByRole('tab', { name: /Modèle/ }))
    expect((screen.getByLabelText('YAML') as HTMLTextAreaElement).value).toMatch(/x-key:\n {2}- pays\n {2}- mode/)
  })

  it('l’Historique ne dépend pas du modèle non enregistré : pas de fenêtre', async () => {
    setup('/referentiels/delais/parametrage')
    await modify()
    await userEvent.setup().click(screen.getByRole('tab', { name: 'Historique' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('Référentiels — tags (ticket 43478978)', () => {
  it('les tags s’affichent dans la liste et la filtrent', async () => {
    setup('/referentiels')
    expect(await screen.findByText('Délais')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filtrer par tag' }), 'contrainte')
    expect(screen.queryByText('Délais')).not.toBeInTheDocument()
    expect(screen.getByText('Tarifs')).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filtrer par tag' }), '')
    expect(screen.getByText('Délais')).toBeInTheDocument()
  })

  it('ajouter et retirer un tag dans le modèle, puis enregistrer', async () => {
    const calls = setup('/referentiels/delais/parametrage')
    const bar = await screen.findByRole('region', { name: 'Tags' })
    expect(within(bar).getByText('aucun tag')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.type(within(bar).getByRole('textbox', { name: 'Nouveau tag' }), 'Contrainte{Enter}')
    await user.type(within(bar).getByRole('textbox', { name: 'Nouveau tag' }), 'contrainte')
    expect(within(bar).getByText('Ce tag est déjà là.')).toBeInTheDocument()
    expect(within(bar).getByRole('button', { name: 'Ajouter' })).toBeDisabled()
    await user.clear(within(bar).getByRole('textbox', { name: 'Nouveau tag' }))
    await user.type(within(bar).getByRole('textbox', { name: 'Nouveau tag' }), 'entreprise')
    await user.click(within(bar).getByRole('button', { name: 'Ajouter' }))
    await user.click(within(bar).getByRole('button', { name: 'Retirer le tag Contrainte' }))
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path === `${BASE}/delais/modele`)).toBe(true))
    const saved = JSON.parse(calls.find((c) => c.method === 'PUT' && c.path === `${BASE}/delais/modele`)!.body!)
    expect(saved.model['x-tags']).toEqual(['entreprise'])
    expect(saved.model['x-key']).toEqual(['pays', 'mode'])
  })
})
