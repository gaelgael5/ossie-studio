import { expect, test } from 'vitest'
import i18n from './i18n'
import { ApiRequestError, errorMessages, translateError } from './errors'

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

test('errorMessages liste les erreurs de champ pour invalid_request', async () => {
  await i18n.changeLanguage('fr')
  const err = new ApiRequestError(
    { error: 'invalid_request', error_args: { fields: ['email_invalid', 'password_too_short'] } },
    400,
  )
  const messages = errorMessages(i18n.t, err)
  expect(messages).toHaveLength(2)
  expect(messages[0]).toContain('email')
})

test('errorMessages rend un message unique pour un code simple', async () => {
  await i18n.changeLanguage('fr')
  const err = new ApiRequestError({ error: 'invalid_credentials' }, 401)
  expect(errorMessages(i18n.t, err)).toEqual(['Email ou mot de passe incorrect.'])
})
