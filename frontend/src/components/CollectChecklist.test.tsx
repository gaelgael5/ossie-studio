import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CollectChecklist } from './CollectChecklist'

const STEPS = ['download', 'scan', 'process', 'resolve', 'write']

describe('CollectChecklist', () => {
  it('scan en cours : étape courante et compteur des fichiers référencés', () => {
    render(
      <CollectChecklist
        steps={STEPS}
        sourceType="git"
        progress={{ step: 'scan', done: ['download'], scanned: 812, total: 0, processed: 0, current: '' }}
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveClass('is-done')
    expect(items[1]).toHaveClass('is-current')
    expect(items[2]).toHaveClass('is-pending')
    expect(screen.getByText('812 fichier(s) référencé(s)')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('traitement : barre de progression et parseur en cours', () => {
    render(
      <CollectChecklist
        steps={STEPS}
        sourceType="git"
        progress={{ step: 'process', done: ['download', 'scan'], scanned: 3128, total: 3128, processed: 782, current: 'C#' }}
      />,
    )
    const bar = screen.getByRole('progressbar')
    expect(bar).toHaveAttribute('value', '782')
    expect(bar).toHaveAttribute('max', '3128')
    expect(screen.getByText('782 / 3128 fichiers traités (25 %) — C#')).toBeInTheDocument()
  })
})
