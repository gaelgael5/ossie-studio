import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { LabelSlugFields, useLabelSlug } from './LabelSlugFields'

function Harness({ initial, locked = false }: { initial?: { label?: string; slug?: string }; locked?: boolean }) {
  const state = useLabelSlug(initial)
  return (
    <>
      <LabelSlugFields idPrefix="x" state={state} labelText="Libellé" slugText="Slug" slugLocked={locked} />
      <output data-testid="final">{state.finalSlug}</output>
    </>
  )
}

describe('LabelSlugFields', () => {
  it('place le libellé avant le slug, et Tab mène du libellé au slug', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.click(screen.getByLabelText('Libellé'))
    await user.tab()
    expect(screen.getByLabelText('Slug')).toHaveFocus()
  })

  it('déduit le slug du libellé à chaque frappe', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('Libellé'), 'Clé GitHub prod')
    expect(screen.getByLabelText('Slug')).toHaveValue('cle-github-prod')
  })

  it('garde la correction manuelle quand le libellé change ensuite', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('Libellé'), 'Forge')
    const slug = screen.getByLabelText('Slug')
    await user.clear(slug)
    await user.type(slug, 'Ma Forge-')
    expect(slug).toHaveValue('ma-forge-')
    expect(screen.getByTestId('final')).toHaveTextContent(/^ma-forge$/)
    await user.type(screen.getByLabelText('Libellé'), ' interne')
    expect(slug).toHaveValue('ma-forge-')
  })

  it('reprend la déduction si le slug est laissé vide en quittant le champ', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const slug = screen.getByLabelText('Slug')
    await user.type(slug, 'manuel')
    await user.clear(slug)
    await user.click(screen.getByLabelText('Libellé'))
    await user.type(screen.getByLabelText('Libellé'), 'Base Data')
    expect(slug).toHaveValue('base-data')
  })

  it('fige le slug existant en édition', () => {
    render(<Harness initial={{ label: 'Nouveau nom', slug: 'ancien-slug' }} locked />)
    expect(screen.getByLabelText('Slug')).toHaveValue('ancien-slug')
    expect(screen.getByLabelText('Slug')).toBeDisabled()
  })
})
