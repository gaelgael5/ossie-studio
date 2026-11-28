import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ImportsApi, ImportTrial, ReferentielImportDetail } from '../apiClient'
import { ApiRequestError } from '../errors'
import { ImportRunPanel } from './ImportRunPanel'

const TAG = { code: 'property_referentiel_unknown', property: 'pays', properties: ['pays'], referentiel: 'pays', title: 'Pays', value: 'XX' }
const REVIEW: ImportTrial = {
  ok: true,
  template_problems: [],
  lines: 4,
  new: 2,
  modified: 1,
  unchanged: 1,
  errors: 0,
  tagged: 2,
  refused: 0,
  tagged_lines: [
    { where: 'ligne 3', key: 'APP-02', kind: 'modified', tags: [{ ...TAG, value: 'ES' }], refused: false },
    { where: 'ligne 4', key: 'APP-03', kind: 'new', tags: [TAG], refused: false },
  ],
  differences: [],
}

function setup() {
  const run = vi.fn()
  run.mockRejectedValueOnce(
    new ApiRequestError({ error: 'import_review_required', error_args: { tagged: 2, report: REVIEW } }, 409),
  )
  run.mockResolvedValueOnce({
    ...REVIEW,
    refused: 1,
    tagged_lines: REVIEW.tagged_lines!.map((l) => ({ ...l, refused: l.where === 'ligne 3' })),
    deletion: 'keep',
    file: 'exemple.csv',
    revision: 'draft:2',
    run_id: 7,
    last_import: null,
  })
  const imports = { base: '/x', invalidates: [], runPath: () => '/run', run } as unknown as ImportsApi
  const detail = { slug: 'fichier', example_size: 10, example_name: 'exemple.csv', deletion: 'keep', format: 'csv', template: '' } as unknown as ReferentielImportDetail
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ImportRunPanel imports={imports} detail={detail} templateSaved />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return run
}

describe('Importer — lignes taguées (ticket 43478978)', () => {
  it('l’import s’arrête sur les lignes taguées ; la personne en refuse une, puis importe', async () => {
    const run = setup()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Importer' }))
    const review = await screen.findByRole('region', { name: 'Lignes taguées à vérifier' })
    expect(review).toHaveTextContent('pays : XX absente du référentiel Pays')
    expect(review).toHaveTextContent('APP-02')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(within(review).getByRole('button', { name: 'Importer toutes les lignes' })).toBeInTheDocument()
    await user.click(within(review).getByRole('checkbox', { name: 'Refuser la ligne 3 (APP-02)' }))
    await user.click(within(review).getByRole('button', { name: 'Importer (1 ligne refusée)' }))
    await waitFor(() => expect(run).toHaveBeenCalledTimes(2))
    expect(run).toHaveBeenLastCalledWith('fichier', undefined, { refuse: ['ligne 3'] })
    expect(await screen.findByText('1 refusée(s)')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Lignes taguées à vérifier' })).not.toBeInTheDocument()
  })
})
