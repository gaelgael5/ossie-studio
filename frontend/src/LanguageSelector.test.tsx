import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test } from 'vitest'
import i18n from './i18n'
import { LanguageSelector } from './LanguageSelector'

beforeEach(async () => {
  await i18n.changeLanguage('fr')
  localStorage.clear()
})

afterEach(async () => {
  await i18n.changeLanguage('fr')
})

test('changer de langue s’applique immédiatement et est mémorisé', async () => {
  render(<LanguageSelector />)
  const select = screen.getByRole('combobox') as HTMLSelectElement
  expect(select.value).toBe('fr')

  await userEvent.selectOptions(select, 'en')

  expect(i18n.resolvedLanguage).toBe('en')
  // Mémorisé par le détecteur (localStorage) sous la clef configurée.
  expect(localStorage.getItem('ossie.lang')).toBe('en')
})

test('une langue non supportée retombe sur l’anglais', async () => {
  await i18n.changeLanguage('de')
  // Le repli de résolution est l'anglais : les libellés sont en anglais.
  expect(i18n.t('healthy')).toBe('Service healthy')
})
