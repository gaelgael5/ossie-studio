import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Combobox } from './Combobox'

const base = {
  id: 'c',
  value: '',
  onChange: vi.fn(),
  placeholder: 'Choisir…',
  filterPlaceholder: 'Filtrer…',
  emptyText: 'Aucun',
  moreText: (n: number) => `${n} de plus`,
}

describe('Combobox', () => {
  it('en chargement : anneau, aria-busy et message, sans « Aucun »', async () => {
    render(<Combobox {...base} options={[]} busy busyText="Chargement des dépôts…" />)
    const trigger = screen.getByRole('button')
    expect(trigger).toHaveAttribute('aria-busy', 'true')
    expect(trigger.querySelector('.spinner')).not.toBeNull()
    await userEvent.click(trigger)
    expect(screen.getByRole('status')).toHaveTextContent('Chargement des dépôts…')
    expect(screen.queryByText('Aucun')).not.toBeInTheDocument()
  })

  it('Échap referme sans choisir', async () => {
    const onChange = vi.fn()
    render(<Combobox {...base} onChange={onChange} options={[{ value: 'a', label: 'A' }]} />)
    await userEvent.click(screen.getByRole('button'))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
})
