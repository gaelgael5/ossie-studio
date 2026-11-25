import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COPY_FEEDBACK_MS, CopyButton } from './CopyButton'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('CopyButton', () => {
  it('copie (API presse-papier) puis montre « ✓ copié » avant de revenir', async () => {
    // user-event installe son propre presse-papier : le nôtre se pose après.
    const user = userEvent.setup()
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    vi.stubGlobal('isSecureContext', true)
    render(<CopyButton text="osk_secret" />)

    await user.click(screen.getByRole('button', { name: 'copier' }))
    expect(writeText).toHaveBeenCalledWith('osk_secret')
    const button = screen.getByRole('button', { name: '✓ copié' })
    expect(button).toHaveClass('is-copied')

    await act(() => new Promise((resolve) => setTimeout(resolve, COPY_FEEDBACK_MS + 100)))
    expect(screen.getByRole('button', { name: 'copier' })).not.toHaveClass('is-copied')
  })

  it('page en HTTP (pas d’API presse-papier) : repli sur la commande « copier »', async () => {
    vi.stubGlobal('isSecureContext', false)
    const execCommand = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })
    const user = userEvent.setup()
    render(<CopyButton text="http://100.74.9.236/mcp" />)

    await user.click(screen.getByRole('button', { name: 'copier' }))
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(screen.getByRole('button', { name: '✓ copié' })).toBeInTheDocument()
  })

  it('copie impossible : le dit', async () => {
    vi.stubGlobal('isSecureContext', false)
    Object.defineProperty(document, 'execCommand', { value: vi.fn(() => false), configurable: true })
    const user = userEvent.setup()
    render(<CopyButton text="x" />)

    await user.click(screen.getByRole('button', { name: 'copier' }))
    expect(screen.getByRole('button', { name: /copie impossible/ })).toHaveClass('is-failed')
  })
})
