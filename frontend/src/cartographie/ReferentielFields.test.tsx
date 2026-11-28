import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { CartoLinkChoices, CartoObjectReferentielLink } from '../apiClient'
import { ReferentielFields, linkedProperties } from './ReferentielFields'
import type { Props } from './FoldedProperties'

const CHOICES: CartoLinkChoices = {
  slug: 'application',
  links: [
    {
      referentiel: 'pays',
      proprietes: { pays: 'code' },
      properties: ['pays'],
      readable: true,
      truncated: false,
      choices: [
        { value: { pays: 'DE' }, label: 'DE', hint: 'Allemagne' },
        { value: { pays: 'FR' }, label: 'FR', hint: 'France' },
      ],
    },
    {
      referentiel: 'sites',
      proprietes: { site_pays: 'pays', site: 'site' },
      properties: ['site_pays', 'site'],
      readable: true,
      truncated: false,
      choices: [{ value: { site_pays: 'FR', site: 'LYON' }, label: 'FR+LYON', hint: '' }],
    },
  ],
}

function setup(values: Props, detailLinks: CartoObjectReferentielLink[] = [], saved: Props = values) {
  const onChange = vi.fn()
  render(
    <MemoryRouter>
      <ReferentielFields choices={CHOICES} values={values} saved={saved} detailLinks={detailLinks} readOnly={false} onChange={onChange} />
    </MemoryRouter>,
  )
  return onChange
}

describe('Fiche d’un objet — valeurs des référentiels (ticket 43478978)', () => {
  it('les propriétés liées sortent de la liste générale', () => {
    expect([...linkedProperties(CHOICES)]).toEqual(['pays', 'site_pays', 'site'])
  })

  it('choisir une valeur ; une clé composée se remplit d’un coup', async () => {
    const onChange = setup({ nom: 'x' })
    const user = userEvent.setup()
    await user.selectOptions(screen.getByRole('combobox', { name: 'pays' }), 'FR — France')
    expect(onChange).toHaveBeenLastCalledWith({ nom: 'x', pays: 'FR' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'site_pays + site' }), 'FR+LYON')
    expect(onChange).toHaveBeenLastCalledWith({ nom: 'x', site_pays: 'FR', site: 'LYON' })
  })

  it('valeur enregistrée présente : état et lien vers le référentiel', () => {
    setup({ pays: 'FR' }, [{ referentiel: 'pays', title: 'Pays', properties: ['pays'], value: 'FR', parts: ['FR'], status: 'found' }])
    expect(screen.getByRole('combobox', { name: 'pays' })).toHaveDisplayValue('FR — France')
    expect(screen.getByText('présente dans le référentiel')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Voir dans le référentiel' })[0]).toHaveAttribute('href', '/referentiels/pays?valeur=FR')
  })

  it('valeur absente : gardée telle quelle et signalée', () => {
    setup({ pays: 'IT' }, [{ referentiel: 'pays', title: 'Pays', properties: ['pays'], value: 'IT', parts: ['IT'], status: 'absent' }])
    expect(screen.getByRole('combobox', { name: 'pays' })).toHaveDisplayValue('IT (absente du référentiel)')
    expect(screen.getByText('absente du référentiel : choisissez une valeur du référentiel')).toBeInTheDocument()
  })
})
