import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CartographiePage } from '../pages/CartographiePage'
import { CartoObjectPage } from './CartoObjectPage'

vi.mock('../workspace', () => ({ useWorkspace: () => ({ spaceId: 's1', branchId: 'b1' }) }))
// BlockNote est remplacé par un champ texte, comme dans les tests de la Curation.
vi.mock('../components/MarkdownEditor', async () => {
  const { useEffect, useRef } = await import('react')
  return {
    default: function FakeEditor({ initial, onReady, label }: { initial: string; onReady: (h: { getMarkdown: () => string }) => void; label: string }) {
      const ref = useRef<HTMLTextAreaElement>(null)
      useEffect(() => {
        onReady({ getMarkdown: () => ref.current?.value ?? '' })
      }, [onReady])
      return <textarea ref={ref} aria-label={`éditeur ${label}`} defaultValue={initial} />
    },
  }
})

const BASE = '/api/spaces/s1/branches/b1/cartographie'
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 12h20"/></svg>'
const el = (name: string, label: string) => ({ name, kind: 'element', layer: 'business', label, definition: '', icon: ICON, attributes: [], relations: [], verbs: { out: '', in: '' } })
const CATALOG = {
  archimate: '3.2',
  layers: [{ key: 'business', label: 'Métier' }],
  base: [],
  types: [
    el('ApplicationComponent', 'Composant applicatif'),
    el('BusinessProcess', 'Processus métier'),
    el('BusinessObject', 'Objet métier'),
    {
      ...el('Access', 'Accès'),
      kind: 'relation',
      layer: '',
      verbs: { out: 'accède à', in: 'accédé par' },
      attributes: [{ name: 'acces', label: 'Sens', type: 'string', values: [{ value: 'Write', label: 'écriture' }, { value: 'Read', label: 'lecture' }], unset: 'non précisé' }],
    },
    { ...el('Triggering', 'Déclenchement'), kind: 'relation', layer: '', verbs: { out: 'déclenche', in: 'déclenché par' } },
  ],
}
const TYPES = [
  { slug: 'application', title: 'Application', archimate: 'ApplicationComponent', path: 'p', properties: 7, objects: 1, state: null },
  { slug: 'processus-de-vente', title: 'Processus de vente', archimate: 'BusinessProcess', path: 'p', properties: 0, objects: 2, state: null },
  { slug: 'document-metier', title: 'Document métier', archimate: 'BusinessObject', path: 'p', properties: 0, objects: 1, state: null },
  { slug: 'acces', title: 'Accès', archimate: 'Access', path: 'p', properties: 0, objects: 1, state: null },
]
const DATES = { created_at: '2026-11-18T10:00:00+00:00', created_by: 'camille.d', updated_at: '2026-11-24T10:00:00+00:00', updated_by: 'alex.r' }
function summary(key: string, type: string, type_title: string, archimate: string, name: string, extra: Record<string, unknown> = {}) {
  return { key, type, type_title, archimate, name, excerpt: '', properties: [], relations: 0, is_relation: false, source: null, target: null, path: `cartographie/objets/${type}/${key.toLowerCase()}.yaml`, dates: DATES, state: null, ...extra }
}
const OBJECTS = [
  summary('PRC-PRISE-COMMANDE', 'processus-de-vente', 'Processus de vente', 'BusinessProcess', 'Prise de commande', { relations: 1 }),
  summary('PRC-PAIEMENT', 'processus-de-vente', 'Processus de vente', 'BusinessProcess', 'Paiement en ligne'),
  summary('OBJ-COMMANDE', 'document-metier', 'Document métier', 'BusinessObject', 'Commande', { relations: 1 }),
  summary('APP-BOUTIQUE', 'application', 'Application', 'ApplicationComponent', 'Boutique en ligne'),
  summary('REL-ECRIT-COMMANDE', 'acces', 'Accès', 'Access', 'Écrit la commande', { is_relation: true, source: 'PRC-PRISE-COMMANDE', target: 'OBJ-COMMANDE' }),
]
const APP_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['criticite'],
  properties: {
    criticite: { type: 'string', enum: ['haute', 'basse'] },
    responsable: { type: 'string' },
    version: { type: 'string' },
    protocole: { type: 'string' },
    duree: { type: 'integer' },
    kpi: { type: 'string' },
    irritants: { type: 'string' },
  },
}
function appDetail(properties: Record<string, unknown>, schema: Record<string, unknown> = APP_SCHEMA) {
  return {
    key: 'APP-BOUTIQUE', type: 'application', type_title: 'Application', archimate: 'ApplicationComponent',
    name: 'Boutique en ligne', documentation: 'Site de vente.\n', properties, source: null, target: null,
    path: 'cartographie/objets/application/app-boutique.yaml', schema,
    relations: { outgoing: [], incoming: [] }, dates: DATES, state: null, problems: [],
  }
}
const PRISE = {
  key: 'PRC-PRISE-COMMANDE', type: 'processus-de-vente', type_title: 'Processus de vente', archimate: 'BusinessProcess',
  name: 'Prise de commande', documentation: '', properties: {}, source: null, target: null,
  path: 'cartographie/objets/processus-de-vente/prc-prise-commande.yaml',
  schema: { type: 'object', properties: {}, additionalProperties: false },
  relations: {
    outgoing: [{ key: 'REL-ECRIT-COMMANDE', name: 'Écrit la commande', type: 'acces', type_title: 'Accès', archimate: 'Access', properties: { acces: 'Write' }, other: { key: 'OBJ-COMMANDE', name: 'Commande', type_title: 'Document métier', exists: true } }],
    incoming: [],
  },
  dates: DATES, state: null, problems: [], processes: ['PRC-PRISE-COMMANDE-DETAIL'],
}

type Call = { method: string; path: string; body?: string }

function setup(url: string, options: { capabilities?: string[]; app?: Record<string, unknown>; schema?: Record<string, unknown> } = {}) {
  const calls: Call[] = []
  const capabilities = options.capabilities ?? ['cartographie:read', 'cartographie:write']
  let app = appDetail(options.app ?? { criticite: 'haute', responsable: 'Équipe Ventes', version: 'v2', protocole: 'REST' }, options.schema)
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      let body: unknown = {}
      if (path === '/api/session') body = { authenticated: true, capabilities }
      else if (path === `${BASE}/catalogue`) body = CATALOG
      else if (path === `${BASE}/types`) body = { readonly: false, types: TYPES }
      else if (path === `${BASE}/types/application`) body = { object_schema: APP_SCHEMA }
      else if (path === `${BASE}/objets` && method === 'GET') body = { readonly: false, objects: OBJECTS }
      else if (path === `${BASE}/modeles`) body = { readonly: false, models: [], rules: [] }
      else if (path === `${BASE}/processus`) body = { readonly: false, processes: [] }
      else if (path === `${BASE}/objets` && method === 'POST') {
        const sent = JSON.parse(String(init?.body)) as Record<string, unknown>
        body = { ...appDetail(sent.properties as Record<string, unknown>), key: sent.key, name: sent.name, state: 'created', dates: null, revision: 'draft:1' }
      } else if (path === `${BASE}/objets/APP-BOUTIQUE` && method === 'PUT') {
        const sent = JSON.parse(String(init?.body)) as { name: string; documentation: string; properties: Record<string, unknown> }
        const kept = Object.fromEntries(Object.entries(sent.properties).filter(([, v]) => v !== '' && v !== undefined && v !== null))
        app = { ...app, name: sent.name, documentation: sent.documentation, properties: kept }
        body = { ...app, revision: 'draft:2' }
      } else if (path === `${BASE}/objets/APP-BOUTIQUE`) body = app
      else if (path === `${BASE}/objets/PRC-PRISE-COMMANDE`) body = PRISE
      else if (path === `${BASE}/objets/PRC-PAIEMENT` && method === 'DELETE') body = { deleted: 'PRC-PAIEMENT' }
      else if (path === `${BASE}/objets/PRC-PAIEMENT`) body = { ...PRISE, key: 'PRC-PAIEMENT', name: 'Paiement en ligne', relations: { outgoing: [], incoming: [] }, processes: [] }
      else if (path === `${BASE}/objets/OBJ-RETENU`) body = { ...PRISE, key: 'OBJ-RETENU', name: 'Objet retenu', relations: { outgoing: [], incoming: [] }, models: [{ slug: 'boutique', name: 'Boutique — vue applicative' }] }
      else if (path.startsWith(`${BASE}/objets/`)) {
        // Toute autre fiche (relation ouverte, objet créé) : une fiche sans relation.
        const key = decodeURIComponent(path.split('/').at(-1)!)
        body = { ...PRISE, key, name: `Fiche ${key}`, relations: { outgoing: [], incoming: [] } }
      }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  function Shown() {
    const { key } = useParams()
    return <p>fiche {key}</p>
  }
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/cartographie/objets" element={<CartographiePage />} />
          <Route path="/cartographie/objets/nouveau" element={<CartoObjectPage />} />
          <Route path="/cartographie/objets/:key" element={<CartoObjectPage />} />
          <Route path="/autre/:key" element={<Shown />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return calls
}

const names = () => screen.getAllByRole('link').filter((a) => a.classList.contains('carto-card-cover')).map((a) => a.getAttribute('aria-label'))

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Cartographie — onglet Objets (ticket 6f71bcba)', () => {
  it('vignettes hors relations, recherche combinable avec un type (critère 2)', async () => {
    setup('/cartographie/objets')
    await screen.findByRole('link', { name: 'Prise de commande' })
    expect(names()).toEqual(['Boutique en ligne', 'Commande', 'Paiement en ligne', 'Prise de commande'])
    const user = userEvent.setup()
    await user.type(screen.getByRole('searchbox', { name: 'Rechercher par nom ou par clé…' }), 'commande')
    expect(names()).toEqual(['Commande', 'Prise de commande'])
    await user.click(screen.getByRole('button', { name: /^Processus de vente/ }))
    expect(names()).toEqual(['Prise de commande'])
  })

  it('pastille d’un type avec son compteur ; pastille des relations (critère 3)', async () => {
    setup('/cartographie/objets')
    await screen.findByRole('link', { name: 'Prise de commande' })
    const pill = screen.getByRole('button', { name: /^Processus de vente/ })
    expect(pill).toHaveTextContent('Processus de vente 2')
    const user = userEvent.setup()
    await user.click(pill)
    expect(names()).toEqual(['Paiement en ligne', 'Prise de commande'])
    await user.click(screen.getByRole('button', { name: /^Relations/ }))
    expect(names()).toEqual(['Écrit la commande'])
    expect(screen.getByText('4 objets · 1 relation')).toBeInTheDocument()
  })
})

describe('Cartographie — fiche d’un objet (ticket 6f71bcba)', () => {
  it('créer APP-BOUTIQUE de type Application (critère 1)', async () => {
    const calls = setup('/cartographie/objets/nouveau')
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText(/Type d’objet/), 'application')
    await user.type(screen.getByLabelText(/^Clé/), 'APP-NOUVELLE')
    await user.type(screen.getByLabelText(/^Nom/), 'Nouvelle application')
    await user.selectOptions(await screen.findByRole('combobox', { name: 'criticite' }), 'haute')
    await user.click(screen.getByRole('button', { name: 'Créer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    // La fiche de l'objet créé s'ouvre.
    expect(await screen.findByRole('heading', { name: 'Fiche APP-NOUVELLE' })).toBeInTheDocument()
    expect(JSON.parse(calls.find((c) => c.method === 'POST')!.body!)).toEqual({
      key: 'APP-NOUVELLE',
      type: 'application',
      name: 'Nouvelle application',
      documentation: '',
      properties: { criticite: 'haute' },
    })
  })

  it('clé prise ou mal formée : refusée à la saisie (critère 4)', async () => {
    setup('/cartographie/objets/nouveau')
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText(/Type d’objet/), 'application')
    await user.type(screen.getByLabelText(/^Nom/), 'X')
    const key = screen.getByLabelText(/^Clé/)
    await user.type(key, 'APP-BOUTIQUE')
    expect(screen.getByText('Cette clé est déjà prise dans l’espace.')).toBeInTheDocument()
    await user.clear(key)
    await user.type(key, 'APP--X')
    expect(screen.getByText(/Majuscules, chiffres et tirets simples seulement/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Créer' })).toBeDisabled()
  })

  it('propriété obligatoire vide : affichée, enregistrement refusé avec la propriété (critère 5)', async () => {
    setup('/cartographie/objets/APP-BOUTIQUE', { app: { responsable: 'Équipe' } })
    await screen.findByRole('heading', { name: 'Boutique en ligne' })
    expect(screen.getByRole('combobox', { name: 'criticite' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('À renseigner avant d’enregistrer : criticite.')
    const user = userEvent.setup()
    await user.clear(screen.getByLabelText(/^Nom/))
    await user.type(screen.getByLabelText(/^Nom/), 'Boutique')
    expect(screen.getByRole('button', { name: 'Enregistrer' })).toBeDisabled()
  })

  it('7 propriétés, 3 renseignées : 3 champs, 4 repliées ; « + Renseigner » (critère 6)', async () => {
    const calls = setup('/cartographie/objets/APP-BOUTIQUE', { app: { criticite: 'haute', version: 'v2', protocole: 'REST' } })
    await screen.findByRole('heading', { name: 'Boutique en ligne' })
    for (const shown of ['criticite', 'version', 'protocole']) expect(screen.getByLabelText(shown)).toBeInTheDocument()
    expect(screen.queryByLabelText('responsable')).not.toBeInTheDocument()
    expect(screen.getByText('4 propriétés non renseignées')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.selectOptions(screen.getByRole('combobox', { name: '+ Renseigner une propriété' }), 'responsable')
    const field = screen.getByLabelText('responsable')
    expect(screen.getByText('3 propriétés non renseignées')).toBeInTheDocument()
    // Enregistrée vide, elle repasse dans la zone repliée.
    await user.type(field, 'x')
    await user.clear(field)
    await user.clear(screen.getByLabelText(/^Nom/))
    await user.type(screen.getByLabelText(/^Nom/), 'Boutique')
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    await screen.findByRole('heading', { name: 'Boutique' })
    expect(screen.queryByLabelText('responsable')).not.toBeInTheDocument()
    expect(screen.getByText('4 propriétés non renseignées')).toBeInTheDocument()
  })

  it('documentation avec une liste à puces relue identique (critère 7)', async () => {
    const calls = setup('/cartographie/objets/APP-BOUTIQUE')
    await screen.findByRole('heading', { name: 'Boutique en ligne' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '✎ Rédiger' }))
    // La documentation affichée passe aussi par l'éditeur (en lecture) : celui de la fenêtre.
    const dialog = await screen.findByRole('dialog', { name: 'Documentation de Boutique en ligne' })
    const editor = await within(dialog).findByRole('textbox')
    const markdown = 'Étapes :\n\n- contrôle du stock ;\n- envoi du courriel.\n'
    await user.clear(editor)
    await user.click(editor)
    await user.paste(markdown)
    await user.click(screen.getByRole('button', { name: 'Appliquer' }))
    expect(screen.getByText('● modifiée')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Enregistrer' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!).documentation).toBe(markdown)
  })

  it('relations en lecture seule : la ligne ouvre la relation, le nom ouvre l’objet lié (critère 8)', async () => {
    setup('/cartographie/objets/PRC-PRISE-COMMANDE')
    const row = await screen.findByRole('link', { name: 'Accès Commande' })
    expect(within(row).getByText('écriture')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Commande' })).toHaveAttribute('href', '/cartographie/objets/OBJ-COMMANDE')
    expect(within(row).queryByRole('textbox')).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(row)
    expect(await screen.findByRole('heading', { name: 'Fiche REL-ECRIT-COMMANDE' })).toBeInTheDocument()
  })

  it('supprimer : refusé si utilisé (relation nommée), sinon le fichier disparaît (critère 9)', async () => {
    setup('/cartographie/objets/PRC-PRISE-COMMANDE')
    await screen.findByRole('heading', { name: 'Prise de commande' })
    expect(screen.getByRole('button', { name: 'Supprimer' })).toBeDisabled()
    expect(screen.getByRole('note')).toHaveTextContent('REL-ECRIT-COMMANDE')
  })

  it('retenu par un modèle : le modèle est nommé, la suppression est refusée (ticket 5a05c7bb, critère 4)', async () => {
    setup('/cartographie/objets/OBJ-RETENU')
    await screen.findByRole('heading', { name: 'Objet retenu' })
    expect(screen.getByRole('button', { name: 'Supprimer' })).toBeDisabled()
    const section = screen.getByRole('region', { name: 'Modèles' })
    expect(within(section).getByRole('link', { name: 'Boutique — vue applicative' })).toHaveAttribute('href', '/cartographie/modeles/boutique')
    expect(within(section).getByRole('note')).toHaveTextContent('Retenu par le modèle Boutique — vue applicative')
  })

  it('supprimer un objet non utilisé', async () => {
    const calls = setup('/cartographie/objets/PRC-PAIEMENT')
    await screen.findByRole('heading', { name: 'Paiement en ligne' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Supprimer' }))
    await user.click(screen.getByRole('button', { name: 'Confirmer la suppression' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
    expect(await screen.findByRole('link', { name: 'Prise de commande' })).toBeInTheDocument()
  })

  it('la description d’une propriété s’affiche sous son champ', async () => {
    const schema = { ...APP_SCHEMA, properties: { ...APP_SCHEMA.properties, criticite: { type: 'string', enum: ['haute', 'basse'], description: 'Impact d’une panne sur les ventes' } } }
    setup('/cartographie/objets/APP-BOUTIQUE', { schema })
    await screen.findByRole('heading', { name: 'Boutique en ligne' })
    expect(screen.getByText('Impact d’une panne sur les ventes')).toBeInTheDocument()
  })

  it('« Ouvrir » sur un élément d’un tableau d’objets l’ouvre sur place', async () => {
    const schema = {
      type: 'object',
      additionalProperties: false,
      properties: {
        serveurs: {
          type: 'array',
          items: {
            type: 'object',
            properties: { nom: { type: 'string' }, ports: { type: 'array', items: { type: 'integer' }, description: 'Ports ouverts' } },
          },
        },
      },
    }
    setup('/cartographie/objets/APP-BOUTIQUE', { schema, app: { serveurs: [{ nom: 'web', ports: [80, 443] }] } })
    await screen.findByRole('heading', { name: 'Boutique en ligne' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'ouvrir ›' }))
    const crumbs = screen.getByRole('navigation', { name: 'Chemin dans la valeur' })
    expect(crumbs).toHaveTextContent('Propriétés › serveurs[0]')
    expect(screen.getByText('Ports ouverts')).toBeInTheDocument()
    expect(screen.getByLabelText('ports[1]')).toHaveValue(443)
    await user.click(within(crumbs).getByRole('button', { name: 'Propriétés' }))
    expect(screen.getByRole('button', { name: 'ouvrir ›' })).toBeInTheDocument()
  })

  it('processus métier : « Ouvrir le détail BPMN » s’il existe, sinon « Créer le détail BPMN » (ticket 7e061c3e)', async () => {
    setup('/cartographie/objets/PRC-PRISE-COMMANDE')
    await screen.findByRole('heading', { name: 'Prise de commande' })
    const section = screen.getByRole('region', { name: 'Détail BPMN' })
    expect(within(section).getByRole('link', { name: 'Ouvrir le détail BPMN' })).toHaveAttribute(
      'href',
      '/cartographie/processus/PRC-PRISE-COMMANDE-DETAIL',
    )
  })

  it('« Créer le détail BPMN » propose le processus métier déjà choisi', async () => {
    setup('/cartographie/objets/PRC-PAIEMENT')
    await screen.findByRole('heading', { name: 'Paiement en ligne' })
    const user = userEvent.setup()
    await user.click(within(screen.getByRole('region', { name: 'Détail BPMN' })).getByRole('button', { name: 'Créer le détail BPMN' }))
    const dialog = screen.getByRole('dialog', { name: 'Nouveau processus' })
    expect(within(dialog).getByLabelText('Détail du processus métier')).toHaveValue('PRC-PAIEMENT')
  })

  it('en lecture : « Aucun détail BPMN. », pas de création', async () => {
    setup('/cartographie/objets/PRC-PAIEMENT', { capabilities: ['cartographie:read'] })
    await screen.findByRole('heading', { name: 'Paiement en ligne' })
    const section = screen.getByRole('region', { name: 'Détail BPMN' })
    expect(within(section).getByText('Aucun détail BPMN.')).toBeInTheDocument()
    expect(within(section).queryByRole('button')).not.toBeInTheDocument()
  })

  it('une application n’a pas de section « Détail BPMN »', async () => {
    setup('/cartographie/objets/APP-BOUTIQUE')
    await screen.findByRole('heading', { name: 'Boutique en ligne' })
    expect(screen.queryByRole('region', { name: 'Détail BPMN' })).not.toBeInTheDocument()
  })

  it('sans le droit de modifier : fiche visible, rien de modifiable (critère 10)', async () => {
    setup('/cartographie/objets/APP-BOUTIQUE', { capabilities: ['cartographie:read'] })
    await screen.findByRole('heading', { name: 'Boutique en ligne' })
    for (const name of ['Enregistrer', 'Supprimer', '✎ Rédiger']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.getByLabelText(/^Nom/)).toHaveAttribute('readonly')
  })
})
