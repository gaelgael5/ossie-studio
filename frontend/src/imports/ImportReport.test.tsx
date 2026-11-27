import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ImportReport } from './ImportReport'
import type { ImportTrial } from '../apiClient'

// Ticket a38d1278 : les erreurs d'un objet de cartographie importé disent la règle.
it('clé invalide et valeur refusée : expliquées, avec la valeur en cause', () => {
  render(
    <ImportReport
      report={{
        ok: false,
        template_problems: [],
        lines: 2,
        errors: 2,
        error_details: [
          { where: 'ligne 3', code: 'object_key_invalid', message: 'smo', path: null, expected: null, field: '' },
          {
            where: 'ligne 15',
            code: 'property_invalid',
            message: "'true' is not of type 'boolean'",
            path: null,
            expected: null,
            field: 'application_phare',
          },
        ],
      }}
    />,
  )
  expect(screen.getByText(/ligne 3/).closest('li')).toHaveTextContent(
    'ligne 3 — clé invalide : majuscules, chiffres et tirets simples (ex. SMO, APP-01) : smo',
  )
  expect(screen.getByText(/ligne 15/).closest('li')).toHaveTextContent(
    "ligne 15 — valeur refusée par le type de la propriété (application_phare) : 'true' is not of type 'boolean'",
  )
})

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
