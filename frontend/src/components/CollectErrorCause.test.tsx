import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { CollectErrorCause, splitScanMessage } from './CollectErrorCause'

describe('CollectErrorCause', () => {
  it('dit la cause, le détail et quoi faire', () => {
    render(<CollectErrorCause cause="too_large" detail="612 KB > 512 KB (taille_max_ko)" />)
    expect(screen.getByText('Document trop volumineux : il n’a pas été copié.')).toBeInTheDocument()
    expect(screen.getByText('612 KB > 512 KB (taille_max_ko)')).toBeInTheDocument()
    expect(screen.getByText(/Taille maximale d’un document/)).toBeInTheDocument()
  })

  it('traduit une cause avec « : » (règle des conventions)', () => {
    render(<CollectErrorCause cause="convention:unknown_key" detail="foo" />)
    expect(screen.getByText('Schéma produit avec une clé non standard.')).toBeInTheDocument()
  })

  it('cause inconnue : le code, jamais une clé i18n brute', () => {
    render(<CollectErrorCause cause="nouvelle_cause" />)
    expect(screen.getByText('Erreur « nouvelle_cause ».')).toBeInTheDocument()
  })

  it('sépare code et détail d’un message de scan', () => {
    expect(splitScanMessage('syntax\nline 3: oops')).toEqual({ cause: 'syntax', detail: 'line 3: oops' })
    expect(splitScanMessage('syntax')).toEqual({ cause: 'syntax', detail: '' })
  })
})
