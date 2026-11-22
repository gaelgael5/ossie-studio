import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BusyButton } from './BusyButton'

describe('BusyButton', () => {
  it('en attente : désactivé, anneau, texte d’attente, aria-busy', () => {
    render(
      <BusyButton className="btn btn-primary" busy busyText="Épreuve en cours…">
        Créer
      </BusyButton>,
    )
    const button = screen.getByRole('button')
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toHaveTextContent('Épreuve en cours…')
    expect(button).toHaveClass('btn-busy')
    expect(button.querySelector('.spinner')).not.toBeNull()
  })

  it('au repos : libellé normal, actif', () => {
    render(<BusyButton busy={false} busyText="…">Créer</BusyButton>)
    const button = screen.getByRole('button', { name: 'Créer' })
    expect(button).toBeEnabled()
    expect(button).toHaveAttribute('aria-busy', 'false')
  })
})
