import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ImportReport } from './ImportReport'
import type { ImportTrial } from '../apiClient'

// Ticket 1612cce4 : un refus du document porte le message de l'écriture commune (le
// même que par MCP) ; une erreur du template garde son libellé.
const REPORT = {
  ok: false,
  template_problems: [],
  lines: 2,
  new: 0,
  modified: 0,
  unchanged: 0,
  errors: 2,
  error_details: [
    {
      where: 'ligne 2',
      code: 'carto_object_invalid',
      message: 'criticite doit être une valeur parmi haute, basse ; reçu "moyenne".',
      path: null,
      expected: null,
      field: '$.proprietes.criticite',
    },
    { where: 'ligne 3', code: 'template_conversion', message: '', path: '$.Delai', expected: 'integer', field: '' },
  ],
  differences: [],
} as unknown as ImportTrial

describe('ImportReport', () => {
  it('affiche le message de l’écriture commune, et le libellé d’une erreur du template', () => {
    render(<ImportReport report={REPORT} />)
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('ligne 2 — criticite doit être une valeur parmi haute, basse ; reçu "moyenne".')
    expect(items[0]).not.toHaveTextContent('carto_object_invalid')
    expect(items[1]).toHaveTextContent('conversion impossible')
  })
})
