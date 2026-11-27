import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { ImportReport } from './ImportReport'

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
