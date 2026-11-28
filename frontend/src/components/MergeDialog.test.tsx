import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MergeDialog } from './MergeDialog'

const BASE = {
  path: 'sources/s/raw/O.schema.yaml',
  state: 'modified',
  changed_upstream: true,
  readonly: false,
  structured: true,
  theirs_absent: false,
  mine_absent: false,
}

function mockApi(view: unknown) {
  const calls: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body as string | undefined })
      const body = method === 'GET' ? view : { path: BASE.path, merged: true }
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(body)) })
    }),
  )
  return calls
}

function renderDialog(onClose = () => {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MergeDialog spaceId="s1" branchId="b1" path={BASE.path} onClose={onClose} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('MergeDialog', () => {
  it('lecture seule : montre la correspondance, sans bouton de validation', async () => {
    mockApi({ ...BASE, changed_upstream: false, readonly: true, kind: 'none', base: { a: 1 }, theirs: { a: 1 }, mine: { a: 2 }, proposed: null, conflicts: [] })
    renderDialog()
    await waitFor(() => expect(screen.getByText('/a')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Valider la fusion' })).not.toBeInTheDocument()
  })

  it('trois colonnes publiée | résultat | brouillon ; conflit « à choisir », puis bascule par les flèches (d727680a)', async () => {
    const calls = mockApi({
      ...BASE,
      kind: 'conflict',
      base: { a: 1, b: 1 },
      theirs: { a: 3, b: 5 },
      mine: { a: 2, b: 1 },
      proposed: { a: 2, b: 5 },
      conflicts: [{ pointer: '/a', base: 1, theirs: 3, mine: 2 }],
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderDialog(onClose)
    const grid = await screen.findByRole('table', { name: 'Fusion : version publiée, résultat, votre brouillon' })
    expect(within(grid).getAllByRole('row')).toHaveLength(2) // /a et /b
    expect(screen.getByText('Publiée')).toBeInTheDocument()
    expect(screen.getByText('Résultat')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('1 conflit à trancher')
    // Bloc fusionné automatiquement : /b vient du côté publié, sa flèche est appliquée.
    expect(screen.getByRole('button', { name: 'Prendre la version publiée (/b)' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByLabelText('Résultat pour /b')).toHaveValue('5')
    // Conflit : à choisir, aucune flèche appliquée, validation désactivée.
    expect(screen.getByLabelText('Résultat pour /a')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Valider la fusion' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Prendre la version publiée (/a)' }))
    expect(screen.getByLabelText('Résultat pour /a')).toHaveValue('3')
    await user.click(screen.getByRole('button', { name: 'Prendre votre version (/a)' }))
    expect(screen.getByLabelText('Résultat pour /a')).toHaveValue('2')
    expect(screen.getByRole('button', { name: 'Prendre votre version (/a)' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('Résolu')
    await user.click(screen.getByRole('button', { name: 'Valider la fusion' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const post = calls.find((c) => c.method === 'POST')
    expect(JSON.parse(post?.body ?? '{}')).toEqual({ path: BASE.path, resolutions: { '/a': { value: 2 } } })
  })

  it('bloc ajouté d’un seul côté : un nouveau clic sur la flèche appliquée le retire, un autre le remet (critère 5)', async () => {
    const calls = mockApi({
      ...BASE,
      kind: 'auto',
      base: { a: 1 },
      theirs: { a: 1 },
      mine: { a: 1, c: 9 },
      proposed: { a: 1, c: 9 },
      conflicts: [],
    })
    const user = userEvent.setup()
    renderDialog()
    const arrow = await screen.findByRole('button', { name: 'Prendre votre version (/c)' })
    expect(arrow).toHaveAttribute('aria-pressed', 'true')
    await user.click(arrow)
    // Retiré : le résultat rejoint la version publiée, où `c` est absent.
    expect(arrow).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Prendre la version publiée (/c)' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(arrow) // remis
    expect(arrow).toHaveAttribute('aria-pressed', 'true')
    await user.click(arrow) // retiré de nouveau
    await user.click(screen.getByRole('button', { name: 'Valider la fusion' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')).toEqual({
      path: BASE.path,
      resolutions: { '/c': { remove: true } },
    })
  })

  it('colonne du milieu modifiable : saisie libre, type vérifié, flèche qui remplace la retouche (edaddc6d)', async () => {
    const calls = mockApi({
      ...BASE,
      kind: 'conflict',
      base: { n: 1, nom: 'a' },
      theirs: { n: 3, nom: 'b' },
      mine: { n: 2, nom: 'c' },
      proposed: { n: 2, nom: 'c' },
      conflicts: [
        { pointer: '/n', base: 1, theirs: 3, mine: 2 },
        { pointer: '/nom', base: 'a', theirs: 'b', mine: 'c' },
      ],
    })
    const user = userEvent.setup()
    renderDialog()
    const n = await screen.findByLabelText('Résultat pour /n')
    await user.type(n, 'abc')
    expect(screen.getByText('Un nombre est attendu.')).toBeInTheDocument()
    await user.clear(n)
    await user.type(n, '42')
    expect(screen.queryByText('Un nombre est attendu.')).not.toBeInTheDocument()
    expect(screen.getAllByText('Modifié à la main').length).toBeGreaterThan(0)
    // Saisie identique à un côté : la flèche de ce côté apparaît appliquée.
    const nom = screen.getByLabelText('Résultat pour /nom')
    await user.type(nom, 'b')
    expect(screen.getByRole('button', { name: 'Prendre la version publiée (/nom)' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Valider la fusion' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')).toEqual({
      path: BASE.path,
      resolutions: { '/n': { value: 42 }, '/nom': { value: 'b' } },
    })
  })

  it('objet raw : deux colonnes en lecture seule, ni flèche ni validation (critère 13)', async () => {
    mockApi({
      ...BASE, collected: true, readonly: true, kind: 'conflict',
      base: { a: 1 }, theirs: { a: 3 }, mine: { a: 2 }, proposed: { a: 2 },
      conflicts: [{ pointer: '/a', base: 1, theirs: 3, mine: 2 }],
    })
    renderDialog()
    expect(await screen.findByText(/Objet collecté/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Prendre/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Valider la fusion' })).not.toBeInTheDocument()
  })

  it('texte en conflit : choisi par une flèche ou retouché ; un YAML cassé ne se valide pas', async () => {
    const calls = mockApi({
      ...BASE, path: 'notes/plan.yaml', structured: false, kind: 'text_conflict',
      base: 'a: 1\n', theirs: 'a: 3\n', mine: 'a: 2\n', proposed: 'a: 2\n', conflicts: [],
    })
    const user = userEvent.setup()
    renderDialog()
    const validate = await screen.findByRole('button', { name: 'Valider la fusion' })
    expect(validate).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /Prendre la version publiée/ }))
    const area = screen.getByLabelText(/Résultat pour/)
    expect(area).toHaveValue('a: 3\n')
    await user.clear(area)
    await user.type(area, 'a: {[}cassé')
    expect(validate).toBeDisabled()
    await user.clear(area)
    await user.type(area, 'a: 4')
    expect(validate).toBeEnabled()
    await user.click(validate)
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')).toEqual({
      path: BASE.path, // la fenêtre est ouverte sur ce chemin (YAML)
      resolutions: { '': { value: 'a: 4' } },
    })
  })

  it('par-dessus la page (bug d45bbc68) ; fichier créé : son contenu, rien à fusionner ; Échap ferme', async () => {
    mockApi({
      ...BASE, path: 'cartographie/types/flux.schema.yaml', state: 'created', changed_upstream: false, readonly: true,
      kind: 'none', base: null, theirs: null, mine: { title: 'Flux' }, theirs_absent: true, proposed: null, conflicts: [],
    })
    const onClose = vi.fn()
    renderDialog(onClose)
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.parentElement).toHaveClass('dialog-backdrop')
    expect(await screen.findByText(/Nouveau fichier : il n’existe pas encore côté publié/)).toBeInTheDocument()
    const user = userEvent.setup()
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
    await user.click(dialog.parentElement!)
    expect(onClose).toHaveBeenCalledTimes(2)
    await user.click(dialog)
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('fichier binaire créé : pas d’aperçu illisible', async () => {
    mockApi({
      ...BASE, path: 'cartographie/imports/t/c.exemple.xlsx', state: 'created', changed_upstream: false, readonly: true,
      kind: 'none', structured: false, base: null, theirs: null, mine: 'PK\u0003\u0004xyz', theirs_absent: true, proposed: null, conflicts: [],
    })
    renderDialog()
    expect(await screen.findByText('Fichier binaire : pas d’aperçu.')).toBeInTheDocument()
    expect(screen.queryByText(/PK/)).not.toBeInTheDocument()
  })

  it('en attendant la réponse : le panneau le dit au lieu de rester blanc', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
    renderDialog()
    expect(await screen.findByText('Chargement…')).toBeInTheDocument()
  })
})
