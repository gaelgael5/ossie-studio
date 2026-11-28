import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { IconButton, IconLink } from './IconButton'

describe('Boutons à icône (ticket 1cca1f06)', () => {
  it('icône dessinée, libellé accessible repris en infobulle, aucun caractère', async () => {
    const onClick = vi.fn()
    render(<IconButton icon="delete" label="Supprimer APP-1" onClick={onClick} />)
    const button = screen.getByRole('button', { name: 'Supprimer APP-1' })
    expect(button).toHaveAttribute('title', 'Supprimer APP-1')
    expect(button.querySelector('svg')).not.toBeNull()
    expect(button.textContent).toBe('')
    expect(button).toHaveClass('btn-icon', 'btn-icon-danger')
    await userEvent.setup().click(button)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('variante lien, pour ouvrir une fiche', () => {
    render(
      <MemoryRouter>
        <IconLink icon="edit" label="Ouvrir APP-1" to="/cartographie/objets/APP-1" />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Ouvrir APP-1' })).toHaveAttribute('href', '/cartographie/objets/APP-1')
  })
})
