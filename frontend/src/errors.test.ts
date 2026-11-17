import { expect, test } from 'vitest'
import i18n from './i18n'
import { translateError } from './errors'

test('traduit un code connu avec ses paramètres', async () => {
  await i18n.changeLanguage('fr')
  const message = translateError(i18n.t, { error: 'secret_slug_taken', error_args: { slug: 'x' } })
  expect(message).toContain('x')
  expect(message).not.toContain('secret_slug_taken')
})

test('un code inconnu retombe sur un message générique montrant le code', async () => {
  await i18n.changeLanguage('fr')
  const message = translateError(i18n.t, { error: 'code_inexistant' })
  expect(message).toContain('code_inexistant')
})
