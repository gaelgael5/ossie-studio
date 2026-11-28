import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { buildTree, DraftBlocks, DraftTree } from './DraftTree'

const objets = Array.from({ length: 25 }, (_, i) => ({
  path: `cartographie/objets/composant-applicatif/app-${String(i).padStart(2, '0')}.yaml`,
  state: 'created',
}))
const FILES = [
  { path: 'cartographie/imports/communique/flux.yaml', state: 'created' },
  { path: 'cartographie/imports/communique/flux.jslt', state: 'modified' },
  { path: 'cartographie/modeles/test2.modele.yaml', state: 'created' },
  ...objets,
  { path: 'README.md', state: 'modified' },
]

describe('Arbre des fichiers du brouillon (ticket 8810604c)', () => {
  it('regroupe par répertoire, fusionne les chaînes à un seul sous-répertoire, trie', () => {
    const root = buildTree(FILES)
    expect(root.files.map((f) => f.path)).toEqual(['README.md'])
    const [carto] = root.dirs
    expect(carto.name).toBe('cartographie')
    expect(carto.dirs.map((d) => d.name)).toEqual(['imports/communique', 'modeles', 'objets/composant-applicatif'])
    expect(carto.dirs[0].files.map((f) => f.path)).toEqual([
      'cartographie/imports/communique/flux.jslt',
      'cartographie/imports/communique/flux.yaml',
    ])
  })

  it('compte par état, replie au-delà de 20 fichiers, signale un conflit replié, ouvre la fusion', async () => {
    const onOpen = vi.fn()
    render(
      <DraftTree
        files={FILES}
        conflicts={new Set(['cartographie/objets/composant-applicatif/app-03.yaml'])}
        onOpen={onOpen}
      />,
    )
    const objetsDir = screen.getByRole('button', { name: 'Déplier objets/composant-applicatif' })
    expect(objetsDir).toHaveAttribute('aria-expanded', 'false')
    expect(objetsDir).toHaveTextContent('25 fichiers · 25 créé')
    expect(within(objetsDir).getByText('en conflit')).toBeInTheDocument()
    expect(screen.queryByText('app-00.yaml')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Replier cartographie' })).toHaveTextContent('28 fichiers · 27 créé · 1 modifié')
    const user = userEvent.setup()
    await user.click(screen.getByText('flux.jslt'))
    expect(onOpen).toHaveBeenCalledWith('cartographie/imports/communique/flux.jslt')
    await user.click(objetsDir)
    expect(screen.getByText('app-00.yaml')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Replier cartographie' }))
    expect(screen.queryByText('flux.jslt')).not.toBeInTheDocument()
  })
})

describe('Blocs de l’écran de publication (ticket 9774a263)', () => {
  const files = [
    ...FILES,
    { path: 'sources/colis/raw/a.schema.yaml', state: 'created' },
    { path: 'governance/classifications/x.yaml', state: 'deleted' },
  ]

  it('un bloc par domaine, dans l’ordre, tous repliés ; « Autres » pour le reste', () => {
    render(<DraftBlocks files={files} conflicts={new Set()} onOpen={() => undefined} />)
    const heads = screen.getAllByRole('button', { name: /^Déplier / })
    expect(heads.map((h) => h.getAttribute('aria-label'))).toEqual([
      'Déplier Cartographie',
      'Déplier Gouvernance',
      'Déplier Sources',
      'Déplier Autres',
    ])
    expect(heads[0]).toHaveTextContent('28 fichiers · 27 créé · 1 modifié')
    expect(screen.queryByText('flux.jslt')).not.toBeInTheDocument()
  })

  it('déplié : l’arbre du bloc, sans répéter son répertoire', async () => {
    const onOpen = vi.fn()
    render(<DraftBlocks files={files} conflicts={new Set()} onOpen={onOpen} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Déplier Cartographie' }))
    const tree = screen.getByRole('list', { name: 'Cartographie' })
    expect(within(tree).getByRole('button', { name: 'Replier imports/communique' })).toBeInTheDocument()
    expect(within(tree).queryByText('cartographie/')).not.toBeInTheDocument()
    await user.click(within(tree).getByText('flux.jslt'))
    expect(onOpen).toHaveBeenCalledWith('cartographie/imports/communique/flux.jslt')
  })
})
