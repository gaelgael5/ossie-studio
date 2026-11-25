import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RawAppModule, RawApplication as RawApplicationData } from '../apiClient'
import { BLOCK_H, BLOCK_W, RawApplication, applicationLayout, contractId } from './RawApplication'
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
