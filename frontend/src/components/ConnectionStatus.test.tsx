import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ConnectionStatus } from './ConnectionStatus'

describe('ConnectionStatus', () => {
  it('distingue « jamais testé » de « valide » et de l’erreur', () => {
    const { rerender } = render(<ConnectionStatus status="never_tested" errorCode={null} />)
    expect(screen.getByText('jamais testé')).toHaveClass('text-warn')
    rerender(<ConnectionStatus status="valid" errorCode={null} />)
    expect(screen.getByText('valide')).toHaveClass('text-ok')
    rerender(<ConnectionStatus status="invalid" errorCode={null} />)
    expect(screen.getByText(/erreur/i)).toHaveClass('text-danger')
  })
})
