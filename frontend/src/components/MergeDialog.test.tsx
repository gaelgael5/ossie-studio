import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MergeDialog, remainingConflicts } from './MergeDialog'

// Le composant graphique (CodeMirror 5) ne se dessine pas dans jsdom : une doublure
// rend ses panneaux en zones de texte, et ses flèches en boutons qui recopient le
// panneau entier dans le résultat. Le rendu réel est vérifié au navigateur.
vi.mock('./CodeMerge', () => ({
  CodeMerge: (props: {
    left?: string | null
    value: string
    right?: string | null
    readOnly?: boolean
    onChange?: (text: string) => void
    takeLeft?: string
    takeRight?: string
    label: string
  }) => {
    const [text, setText] = useState(props.value)
    const put = (value: string) => {
      setText(value)
      props.onChange?.(value)
    }
    return (
      <div data-testid="code-merge">
        {props.left != null ? <pre data-testid="pane-left">{props.left}</pre> : null}
        {props.left != null && !props.readOnly ? (
          <button type="button" aria-label={props.takeLeft} onClick={() => put(props.left ?? '')}>
            →
          </button>
        ) : null}
        <textarea aria-label={props.label} readOnly={props.readOnly} value={text} onChange={(e) => put(e.target.value)} />
        {props.right != null && !props.readOnly ? (
          <button type="button" aria-label={props.takeRight} onClick={() => put(props.right ?? '')}>
            ←
          </button>
        ) : null}
        {props.right != null ? <pre data-testid="pane-right">{props.right}</pre> : null}
      </div>
    )
  },
}))

const MARKER = '⟨ à choisir ⟩'
const BASE = {
  path: 'modeles/client.modele.yaml',
  state: 'modified',
  changed_upstream: true,
  readonly: false,
  structured: true,
  theirs_absent: false,
  mine_absent: false,
  base: null,
  theirs: null,
  mine: null,
  proposed: null,
  conflicts: [],
  marker: MARKER,
  auto: 0,
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

const GRID = 'Fusion : version publiée, résultat, votre brouillon'

afterEach(() => vi.unstubAllGlobals())

describe('remainingConflicts', () => {
  it('compte les marques du serveur et les blocs de git merge-file', () => {
    expect(remainingConflicts(`a: ${MARKER}\nb: ${MARKER}\n`, MARKER)).toBe(2)
    expect(remainingConflicts('<<<<<<< brouillon\na\n=======\nb\n>>>>>>> publié\n', MARKER)).toBe(1)
    expect(remainingConflicts('a: 1\n', MARKER)).toBe(0)
  })
})

describe('MergeDialog', () => {
  it('trois panneaux publiée | résultat | brouillon ; badge ; validation bloquée tant qu’une marque reste ; texte envoyé tel quel (fcf32f4a)', async () => {
    const calls = mockApi({
      ...BASE,
      kind: 'conflict',
      auto: 2,
      texts: {
        base: 'segment: Segment\n',
        theirs: 'segment: Niveau\npays: FR\n',
        mine: 'segment: Commercial\nemail: x\n',
        proposed: `segment: ${MARKER}\npays: FR\nemail: x\n`,
      },
    })
    const onClose = vi.fn()
    const user = userEvent.setup()
    renderDialog(onClose)
    const middle = await screen.findByLabelText(GRID)
    expect(screen.getByText('Publiée')).toBeInTheDocument()
    expect(screen.getByText('Résultat')).toBeInTheDocument()
    expect(screen.getByText('Mon brouillon')).toBeInTheDocument()
    expect(screen.getByTestId('pane-left')).toHaveTextContent('segment: Niveau')
    expect(screen.getByTestId('pane-right')).toHaveTextContent('segment: Commercial')
    expect(middle).toHaveValue(`segment: ${MARKER}\npays: FR\nemail: x\n`)
    expect(screen.getByRole('status')).toHaveTextContent('1 conflit à trancher · 2 fusions auto')
    const validate = screen.getByRole('button', { name: 'Valider la fusion' })
    expect(validate).toBeDisabled()
    await user.clear(middle)
    await user.type(middle, 'segment: Niveau commercial\npays: FR\nemail: x\n')
    expect(screen.getByRole('status')).toHaveTextContent('Conflits résolus · 2 fusions auto')
    expect(validate).toBeEnabled()
    await user.click(validate)
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')).toEqual({
      path: BASE.path,
      content: 'segment: Niveau commercial\npays: FR\nemail: x\n',
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('flèches libellées ; un YAML cassé ne se valide pas', async () => {
    mockApi({
      ...BASE,
      kind: 'conflict',
      texts: { base: 'a: 1\n', theirs: 'a: 3\n', mine: 'a: 2\n', proposed: `a: ${MARKER}\n` },
    })
    const user = userEvent.setup()
    renderDialog()
    const middle = await screen.findByLabelText(GRID)
    await user.click(screen.getByRole('button', { name: 'Prendre la version publiée' }))
    expect(middle).toHaveValue('a: 3\n')
    const validate = screen.getByRole('button', { name: 'Valider la fusion' })
    expect(validate).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Prendre votre version' }))
    expect(middle).toHaveValue('a: 2\n')
    await user.clear(middle)
    await user.type(middle, 'a: {[}cassé')
    expect(validate).toBeDisabled()
  })

  it('« Afficher la base » montre l’ancêtre commun sans perdre le résultat ; sans base, bouton désactivé', async () => {
    mockApi({
      ...BASE,
      kind: 'conflict',
      texts: { base: 'a: depart\n', theirs: 'a: 3\n', mine: 'a: 2\n', proposed: `a: ${MARKER}\n` },
    })
    const user = userEvent.setup()
    const view = renderDialog()
    const middle = await screen.findByLabelText(GRID)
    await user.clear(middle)
    await user.type(middle, 'a: 9\n')
    await user.click(screen.getByRole('button', { name: 'Afficher la base' }))
    expect(screen.getByLabelText('Départ')).toHaveValue('a: depart\n')
    expect(screen.getByLabelText(GRID)).toHaveValue('a: 9\n')
    await user.click(screen.getByRole('button', { name: 'Masquer la base' }))
    expect(screen.queryByLabelText('Départ')).not.toBeInTheDocument()
    view.unmount()

    mockApi({ ...BASE, kind: 'conflict', texts: { base: null, theirs: 'a: 3\n', mine: 'a: 2\n', proposed: `a: ${MARKER}\n` } })
    renderDialog()
    const button = await screen.findByRole('button', { name: 'Afficher la base' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', expect.stringMatching(/Pas de base/))
  })

  it('lecture seule (rien de changé côté publié) : deux panneaux, sans flèche ni validation', async () => {
    mockApi({
      ...BASE, changed_upstream: false, readonly: true, kind: 'none',
      texts: { base: 'a: 1\n', theirs: 'a: 1\n', mine: 'a: 2\n', proposed: null },
    })
    renderDialog()
    expect(await screen.findByText(/Rien n’a changé côté publié/)).toBeInTheDocument()
    expect(screen.getByTestId('pane-right')).toHaveTextContent('a: 2')
    expect(screen.queryByRole('button', { name: /Prendre/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Valider la fusion' })).not.toBeInTheDocument()
  })

  it('objet raw : deux panneaux en lecture seule, ni flèche ni validation (critère 13)', async () => {
    mockApi({
      ...BASE, collected: true, readonly: true, kind: 'conflict',
      texts: { base: 'a: 1\n', theirs: 'a: 3\n', mine: 'a: 2\n', proposed: `a: ${MARKER}\n` },
    })
    renderDialog()
    expect(await screen.findByText(/Objet collecté/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Prendre/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Valider la fusion' })).not.toBeInTheDocument()
  })

  it('supprimé côté publié : la flèche du côté absent accepte la suppression', async () => {
    const calls = mockApi({
      ...BASE, kind: 'deleted_upstream', theirs_absent: true, theirs: null, mine: { a: 2 },
      texts: { base: 'a: 1\n', theirs: null, mine: 'a: 2\n', proposed: 'a: 2\n' },
    })
    const user = userEvent.setup()
    renderDialog()
    await user.click(await screen.findByRole('button', { name: 'Accepter la suppression' }))
    await user.click(screen.getByRole('button', { name: 'Valider la fusion' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.method === 'POST')?.body ?? '{}')).toEqual({ path: BASE.path, delete: true })
  })

  it('par-dessus la page (bug d45bbc68) ; fichier créé : un seul panneau ; Échap ferme', async () => {
    mockApi({
      ...BASE, state: 'created', changed_upstream: false, readonly: true, kind: 'none', theirs_absent: true,
      texts: { base: null, theirs: null, mine: 'title: Flux\n', proposed: null },
    })
    const onClose = vi.fn()
    renderDialog(onClose)
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.parentElement).toHaveClass('dialog-backdrop')
    expect(await screen.findByText(/Nouveau fichier : il n’existe pas encore côté publié/)).toBeInTheDocument()
    expect(screen.getByLabelText(GRID)).toHaveValue('title: Flux\n')
    expect(screen.queryByTestId('pane-right')).not.toBeInTheDocument()
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
      kind: 'none', structured: false, theirs_absent: true,
      texts: { base: null, theirs: null, mine: 'PK\u0003\u0004xyz', proposed: null },
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
