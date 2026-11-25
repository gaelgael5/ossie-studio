import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RawAppModule, RawApplication as RawApplicationData } from '../apiClient'
import {
  BLOCK_H,
  BLOCK_W,
  RawApplication,
  anchorPoint,
  applicationLayout,
  autoAnchors,
  contractId,
  linkAnchors,
  linkPath,
} from './RawApplication'
import { RawModulePreview } from './RawModulePreview'

function mod(title: string, nature: string, deps: string[] = []): RawAppModule {
  return {
    id: `module-${title.toLowerCase()}.schema.yaml`,
    file: `module-${title.toLowerCase()}.schema.yaml`,
    title,
    language: 'csharp',
    nature,
    targets: ['net8.0'],
    project: `src/${title}/${title}.csproj`,
    folder: `src/${title}`,
    dependencies: deps.map((d) => ({ id: `module-${d.toLowerCase()}.schema.yaml`, project: `src/${d}/${d}.csproj` })),
    dependents: [],
    counts: { classes: 9, entry_points: 14, http: nature === 'web' ? 13 : 0, methods: 31, functions: 0 },
  }
}

const APP: RawApplicationData = {
  modules: [
    mod('Api', 'web', ['Application', 'Domain']),
    mod('Application', 'library', ['Domain', 'Infra']),
    mod('Domain', 'library'),
    mod('Infra', 'library', ['Domain']),
    mod('Worker', 'worker', ['Domain']),
    mod('Database', 'sql'),
    mod('Tests', 'test', ['Api', 'Domain']),
  ],
  contracts: [{ path: 'contracts/boutique/contrat.full.openapi.yaml', title: 'Boutique', operations: 14, linked: 3 }],
}

const at = (layout: ReturnType<typeof applicationLayout>, title: string) =>
  layout.blocks.get(`module-${title.toLowerCase()}.schema.yaml`)!

describe('Vue « Application » — disposition calculée (ticket 0fa620be)', () => {
  it('colonnes selon les dépendances : entrées à gauche, cœur à droite', () => {
    const layout = applicationLayout(APP)
    expect(at(layout, 'Api').x).toBe(at(layout, 'Worker').x)
    expect(at(layout, 'Api').x).toBeLessThan(at(layout, 'Application').x)
    expect(at(layout, 'Application').x).toBeLessThan(at(layout, 'Infra').x)
    expect(at(layout, 'Infra').x).toBeLessThan(at(layout, 'Domain').x)
  })

  it('projets de test et contrat sur une rangée à part, en bas', () => {
    const layout = applicationLayout(APP)
    const main = APP.modules.filter((m) => m.nature !== 'test').map((m) => layout.blocks.get(m.id)!.y)
    const tests = at(layout, 'Tests')
    const contract = layout.blocks.get(contractId(APP.contracts[0].path))!
    expect(tests.y).toBeGreaterThan(Math.max(...main))
    expect(contract.y).toBe(tests.y)
    expect(contract.x).toBeLessThan(tests.x)
  })

  it('sans chevauchement, et la même à chaque ouverture (ordre reçu indifférent)', () => {
    const layout = applicationLayout(APP)
    const blocks = [...layout.blocks.values()]
    for (const a of blocks)
      for (const b of blocks) {
        if (a === b) continue
        const overlap = a.x < b.x + BLOCK_W && b.x < a.x + BLOCK_W && a.y < b.y + BLOCK_H && b.y < a.y + BLOCK_H
        expect(overlap).toBe(false)
      }
    const shuffled = applicationLayout({ ...APP, modules: [...APP.modules].reverse() })
    expect([...shuffled.blocks.entries()].sort()).toEqual([...layout.blocks.entries()].sort())
    expect(blocks.every((b) => b.x + BLOCK_W <= layout.width && b.y + BLOCK_H <= layout.height)).toBe(true)
  })

  it('une boucle de dépendances ne bloque pas le calcul', () => {
    const loop = applicationLayout({ modules: [mod('A', 'library', ['B']), mod('B', 'library', ['A'])], contracts: [] })
    expect(loop.blocks.size).toBe(2)
  })
})

function ok(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
}

function wrap(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('Vue « Application » — interactions', () => {
  it('clic sur un bloc : aperçu ; clic sur « n classes » : menu de gauche', async () => {
    vi.stubGlobal('fetch', vi.fn(() => ok(APP)))
    const onSelect = vi.fn()
    const onOpenClasses = vi.fn()
    wrap(
      <RawApplication spaceId="s1" branchId="b1" slug="app" selected={APP.modules[0].file} onSelect={onSelect}
        onOpenClasses={onOpenClasses} />,
    )
    const user = userEvent.setup()
    const api = await screen.findByRole('button', { name: 'Module Api' })
    expect(api).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/Couche raw/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Module Domain' }))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ title: 'Domain' }))
    await user.click(screen.getByRole('link', { name: /9 classe\(s\) de Infra/ }))
    expect(onOpenClasses).toHaveBeenCalledWith(expect.objectContaining({ title: 'Infra' }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    // Projet SQL : ses objets et ses procédures.
    expect(screen.getByRole('link', { name: /9 objet\(s\) de Database/ })).toBeInTheDocument()
    // Bloc du contrat : opérations reliées au code (ticket 90ed0967).
    expect(screen.getByText('3 / 14 opérations reliées au code')).toBeInTheDocument()
  })
})

describe('Aperçu d’un module', () => {
  it('points d’entrée, état au contrat, méthodes par classe, dépendance externe', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        ok({
          ...mod('Api', 'web'),
          dependencies: [
            { id: 'module-domain.schema.yaml', project: 'src/Domain/Domain.csproj', title: 'Domain' },
            { id: null, project: '../Externe/Outils.csproj', title: null },
          ],
          dependents: [{ id: 'module-tests.schema.yaml', title: 'Tests' }],
          entry_points: [
            { kind: 'http', verb: 'GET', route: '/api/commandes/{id}', method: 'Lire', owner: 'CommandesController', contract: null },
            { kind: 'http', verb: 'PUT', route: null, method: 'Changer', owner: 'CommandesController',
              contract: { status: 'absent', operations: [] } },
            { kind: 'http', verb: 'DELETE', route: '/paniers/{id}', method: 'Vider', owner: null,
              contract: { status: 'linked', operations: [{ file: 'c.yaml', operationId: 'viderPanier', verb: 'DELETE', path: '/paniers/{id}' }] } },
            { kind: 'task', verb: null, route: null, method: null, owner: 'Nettoyage', contract: null },
          ],
          classes: [
            { title: 'CommandesController', file: 'c.schema.yaml',
              methods: [{ name: 'Lire', signature: 'Lire(id: Guid): Commande', entry: true }] },
          ],
        }),
      ),
    )
    wrap(<RawModulePreview spaceId="s1" branchId="b1" slug="app" file="module-api.schema.yaml" onCollapse={() => {}} />)
    expect(await screen.findByText('src/Api/Api.csproj')).toBeInTheDocument()
    expect(screen.getByText('application web · net8.0')).toBeInTheDocument()
    // Routes comparées au contrat : une reliée sur deux (la troisième non comparée).
    expect(screen.getByText('1 / 2 route(s) au contrat')).toBeInTheDocument()
    expect(screen.getByText('../Externe/Outils.csproj (hors de la source)')).toBeInTheDocument()
    expect(screen.getByText('Points d’entrée · 4')).toBeInTheDocument()
    expect(screen.getByText('/api/commandes/{id}')).toBeInTheDocument()
    expect(screen.getByText('CommandesController.Lire')).toBeInTheDocument()
    expect(screen.getByText('non comparé')).toBeInTheDocument()
    expect(screen.getByText('route inconnue')).toBeInTheDocument()
    expect(screen.getByText(/absent du contrat/)).toBeInTheDocument()
    expect(screen.getByText(/viderPanier/)).toBeInTheDocument()
    expect(screen.getByText('Vider (minimal API)')).toBeInTheDocument()
    expect(screen.getByText('tâche')).toBeInTheDocument()
    expect(screen.getByText('Méthodes publiques · 1')).toBeInTheDocument()
    expect(screen.getByText(/Lire\(id: Guid\): Commande/)).toBeInTheDocument()
    expect(screen.getByText('ENTRÉE')).toBeInTheDocument()
  })
})

describe('Aperçu d’un projet SQL (ticket 30c55418)', () => {
  it('procédures et fonctions du module, sans section de méthodes vide', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        ok({
          ...mod('Database', 'sql'),
          dependencies: [],
          dependents: [],
          entry_points: [],
          functions: [
            { name: 'dbo.LireCommande', signature: 'dbo.LireCommande(@Id: UNIQUEIDENTIFIER)', entry: false },
            { name: 'dbo.Total', signature: 'dbo.Total(@CommandeId: INT): DECIMAL(18,2)', entry: false },
          ],
          classes: [],
        }),
      ),
    )
    wrap(<RawModulePreview spaceId="s1" branchId="b1" slug="app" file="module-database.schema.yaml" onCollapse={() => {}} />)
    expect(await screen.findByText('Procédures et fonctions · 2')).toBeInTheDocument()
    expect(screen.getByText(/dbo\.Total\(@CommandeId: INT\): DECIMAL\(18,2\)/)).toBeInTheDocument()
    expect(screen.queryByText(/Méthodes publiques/)).not.toBeInTheDocument()
  })
})

describe('Ancres des liens (ticket a1fd0663)', () => {
  const layout = applicationLayout(APP)
  const api = at(layout, 'Api')
  const domain = at(layout, 'Domain')
  const end = (d: string) => d.trim().split(' ').at(-1)!.split(',').map(Number)

  it('ancres calculées : du côté droit au côté gauche, même tracé qu’avant', () => {
    const [a, b] = autoAnchors(api, domain)
    expect([a.side, b.side]).toEqual(['right', 'left'])
    const p1 = anchorPoint(api, a)
    const p2 = anchorPoint(domain, b)
    const mid = (p1.x + p2.x) / 2
    expect(linkPath(p1, a.side, p2, b.side)).toBe(`M${p1.x},${p1.y} C${mid},${p1.y} ${mid},${p2.y} ${p2.x},${p2.y}`)
    // Une ancre choisie remplace la seule extrémité concernée.
    expect(linkAnchors(api, domain, { from: 'auto', to: { side: 'top', at: 0.5 } })).toEqual([
      { side: 'right', at: 0.5 },
      { side: 'top', at: 0.5 },
    ])
  })

  function routes(app: RawApplicationData, capabilities: string[]) {
    return vi.fn((url: string, init?: { method?: string; body?: string }) => {
      if (url.includes('/api/session')) return ok({ authenticated: true, bootstrap_required: false, user: null, capabilities })
      if (init?.method === 'PUT') return ok(JSON.parse(init.body ?? '{}'))
      return ok(app)
    })
  }
  const edgePath = () =>
    screen.getByRole('button', { name: 'Lien Api → Domain' }).querySelector('path:not(.raw-app-hit)')!.getAttribute('d')!

  it('à l’ouverture, l’ancre enregistrée est appliquée', async () => {
    const withAnchor = {
      ...APP,
      modules: APP.modules.map((m) =>
        m.title === 'Api' ? { ...m, anchors: { [domain.id]: { from: 'auto' as const, to: { side: 'top' as const, at: 0.5 } } } } : m,
      ),
    }
    vi.stubGlobal('fetch', routes(withAnchor, []))
    wrap(<RawApplication spaceId="s1" branchId="b1" slug="app" selected="" onSelect={() => {}} onOpenClasses={() => {}} />)
    await screen.findByRole('button', { name: 'Lien Api → Domain' })
    const top = anchorPoint(domain, { side: 'top', at: 0.5 })
    expect(end(edgePath())).toEqual([top.x, top.y])
  })

  it('clic sur un lien, puis sur une ancre : rendu immédiat et écriture ; retour à l’ancre calculée', async () => {
    const fetch = routes(APP, ['sources:read', 'sources:collect'])
    vi.stubGlobal('fetch', fetch)
    wrap(<RawApplication spaceId="s1" branchId="b1" slug="app" selected="" onSelect={() => {}} onOpenClasses={() => {}} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Lien Api → Domain' }))
    expect(screen.getByRole('button', { name: 'Lien Api → Domain' })).toHaveAttribute('aria-pressed', 'true')
    // Les ancres des deux blocs, la courante marquée.
    expect(screen.getByRole('button', { name: 'Ancre gauche 50 % de Domain' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByRole('button', { name: /^Ancre .* de (Api|Domain)$/ })).toHaveLength(16)
    expect(screen.getByRole('button', { name: 'Ancre calculée' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Ancre haut 50 % de Domain' }))
    const top = anchorPoint(domain, { side: 'top', at: 0.5 })
    expect(end(edgePath())).toEqual([top.x, top.y])
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(put[0]).toContain('/raw-explorer/app/module/link')
    expect(JSON.parse(put[1]!.body!)).toEqual({
      module: 'module-api.schema.yaml',
      target: 'module-domain.schema.yaml',
      from: 'auto',
      to: { side: 'top', at: 0.5 },
    })
  })

  it('remettre l’ancre calculée écrit auto aux deux bouts', async () => {
    const withAnchor = {
      ...APP,
      modules: APP.modules.map((m) =>
        m.title === 'Api' ? { ...m, anchors: { [domain.id]: { from: { side: 'bottom' as const, at: 0.25 }, to: 'auto' as const } } } : m,
      ),
    }
    const fetch = routes(withAnchor, ['sources:collect'])
    vi.stubGlobal('fetch', fetch)
    wrap(<RawApplication spaceId="s1" branchId="b1" slug="app" selected="" onSelect={() => {}} onOpenClasses={() => {}} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Lien Api → Domain' }))
    await user.click(screen.getByRole('button', { name: 'Ancre calculée' }))
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(put[1]!.body!)).toMatchObject({ from: 'auto', to: 'auto' })
    const left = anchorPoint(domain, { side: 'left', at: 0.5 })
    expect(end(edgePath())).toEqual([left.x, left.y])
  })

  it('sans le droit de collecter : le lien se sélectionne, sans poignée ni ancre', async () => {
    vi.stubGlobal('fetch', routes(APP, ['sources:read']))
    wrap(<RawApplication spaceId="s1" branchId="b1" slug="app" selected="" onSelect={() => {}} onOpenClasses={() => {}} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Lien Api → Domain' }))
    expect(screen.getByRole('group', { name: 'Lien Api → Domain' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Ancre / })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ancre calculée' })).not.toBeInTheDocument()
  })
})

describe('Glisser une poignée (ticket a1fd0663)', () => {
  // jsdom ne calcule pas la géométrie SVG : écran et dessin coïncident (zoom 1).
  const identity = { inverse: () => identity }
  beforeEach(() => {
    // jsdom n'a pas PointerEvent : sans lui, les coordonnées du pointeur sont perdues.
    class PointerEventShim extends MouseEvent {
      pointerId: number
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init)
        this.pointerId = init.pointerId ?? 0
      }
    }
    vi.stubGlobal('PointerEvent', PointerEventShim)
    Object.assign(SVGSVGElement.prototype, {
      getScreenCTM: () => identity,
      createSVGPoint: () => ({ x: 0, y: 0, matrixTransform(this: { x: number; y: number }) { return { x: this.x, y: this.y } } }),
      setPointerCapture: () => {},
    })
  })

  function routes(capabilities: string[]) {
    return vi.fn((url: string, init?: { method?: string; body?: string }) => {
      if (url.includes('/api/session')) return ok({ authenticated: true, bootstrap_required: false, user: null, capabilities })
      if (init?.method === 'PUT') return ok(JSON.parse(init.body ?? '{}'))
      return ok(APP)
    })
  }

  it('lâchée sur une ancre du même bloc, la poignée s’y accroche ; ailleurs, retour', async () => {
    const fetch = routes(['sources:collect'])
    vi.stubGlobal('fetch', fetch)
    wrap(<RawApplication spaceId="s1" branchId="b1" slug="app" selected="" onSelect={() => {}} onOpenClasses={() => {}} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Lien Api → Domain' }))
    const layout = applicationLayout(APP)
    const domain = at(layout, 'Domain')
    const svg = screen.getByRole('img', { name: /./ })
    const handle = () => screen.getByLabelText(/^Arrivée du lien/)
    const left = anchorPoint(domain, { side: 'left', at: 0.5 })
    // Lâchée loin de toute ancre : rien n'est écrit.
    fireEvent.pointerDown(handle(), { clientX: left.x, clientY: left.y, pointerId: 1 })
    fireEvent.pointerMove(svg, { clientX: left.x + 60, clientY: left.y + 200, pointerId: 1 })
    fireEvent.pointerUp(svg, { clientX: left.x + 60, clientY: left.y + 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
    // Lâchée près de l'ancre bas-droite de Domain : elle s'y accroche.
    const target = anchorPoint(domain, { side: 'bottom', at: 0.75 })
    fireEvent.pointerDown(handle(), { clientX: left.x, clientY: left.y, pointerId: 1 })
    fireEvent.pointerMove(svg, { clientX: target.x + 5, clientY: target.y + 4, pointerId: 1 })
    fireEvent.pointerUp(svg, { clientX: target.x + 5, clientY: target.y + 4, pointerId: 1 })
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true))
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(put[1]!.body!)).toMatchObject({ from: 'auto', to: { side: 'bottom', at: 0.75 } })
    // Le lien reste sélectionné (le lâcher ne désélectionne pas).
    expect(screen.getByRole('button', { name: 'Lien Api → Domain' })).toHaveAttribute('aria-pressed', 'true')
  })
})
