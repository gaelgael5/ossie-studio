import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { Link, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { UnsavedProvider, useDirty, useSaveShortcut, useUnsaved } from './unsaved'

// Bug 5a6b0213 : demander s'il faut enregistrer avant de quitter l'écran.

function Editor({ save, shortcut = 'model' }: { save: () => Promise<unknown>; shortcut?: string }) {
  const [dirty, setDirty] = useState(false)
  useUnsaved('model', { label: 'Modèle', dirty, saveable: true }, save)
  useSaveShortcut(() => shortcut)
  const marked = useDirty('model').length > 0
  return (
    <>
      <button type="button" onClick={() => setDirty(true)}>
        modifier
      </button>
      {marked ? <span>pastille</span> : null}
      <Link to="/ailleurs">partir</Link>
    </>
  )
}

function setup(save: () => Promise<unknown> = () => Promise.resolve(), shortcut = 'model') {
  const router = createMemoryRouter(
    [
      {
        path: '/ecran',
        element: (
          <UnsavedProvider>
            <Editor save={save} shortcut={shortcut} />
          </UnsavedProvider>
        ),
      },
      { path: '/ailleurs', element: <p>ailleurs</p> },
    ],
    { initialEntries: ['/ecran'] },
  )
  render(<RouterProvider router={router} />)
  return router
}

describe('garde de sortie (bug 5a6b0213)', () => {
  it('sans modification : on part sans question', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(screen.getByRole('link', { name: 'partir' }))
    expect(screen.getByText('ailleurs')).toBeInTheDocument()
  })

  it('avec modification : Rester, puis Quitter sans enregistrer', async () => {
    const user = userEvent.setup()
    const save = vi.fn(() => Promise.resolve())
    setup(save)
    await user.click(screen.getByRole('button', { name: 'modifier' }))
    expect(screen.getByText('pastille')).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'partir' }))
    expect(screen.getByRole('dialog', { name: 'Modifications non enregistrées' })).toBeInTheDocument()
    expect(screen.getByText('Modèle')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Rester' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('ailleurs')).not.toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'partir' }))
    await user.click(screen.getByRole('button', { name: 'Quitter sans enregistrer' }))
    expect(await screen.findByText('ailleurs')).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })

  it('Enregistrer et quitter : enregistre puis part ; un refus fait rester', async () => {
    const user = userEvent.setup()
    const save = vi.fn().mockRejectedValueOnce(new Error('refus')).mockResolvedValueOnce(undefined)
    setup(save)
    await user.click(screen.getByRole('button', { name: 'modifier' }))
    await user.click(screen.getByRole('link', { name: 'partir' }))
    await user.click(screen.getByRole('button', { name: 'Enregistrer et quitter' }))
    expect(save).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('ailleurs')).not.toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'partir' }))
    await user.click(screen.getByRole('button', { name: 'Enregistrer et quitter' }))
    expect(await screen.findByText('ailleurs')).toBeInTheDocument()
    expect(save).toHaveBeenCalledTimes(2)
  })
})

describe('Cmd+S / Ctrl+S (ticket 718f28e5)', () => {
  const press = (init: KeyboardEventInit) => {
    const event = new KeyboardEvent('keydown', { key: 's', bubbles: true, cancelable: true, ...init })
    window.dispatchEvent(event)
    return event
  }

  it('enregistre l’onglet affiché quand il est modifié, et bloque le navigateur', async () => {
    const user = userEvent.setup()
    const save = vi.fn(() => Promise.resolve())
    setup(save)
    // Sans modification : intercepté, rien d'enregistré.
    expect(press({ ctrlKey: true }).defaultPrevented).toBe(true)
    expect(save).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'modifier' }))
    press({ ctrlKey: true })
    press({ metaKey: true })
    expect(save).toHaveBeenCalledTimes(2)
    // Une autre touche n'est pas interceptée.
    expect(press({ key: 'a', ctrlKey: true }).defaultPrevented).toBe(false)
  })

  it('n’enregistre pas un onglet qui n’est pas affiché', async () => {
    const user = userEvent.setup()
    const save = vi.fn(() => Promise.resolve())
    setup(save, 'values')
    await user.click(screen.getByRole('button', { name: 'modifier' }))
    press({ ctrlKey: true })
    expect(save).not.toHaveBeenCalled()
  })
})
