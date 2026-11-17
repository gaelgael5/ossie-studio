import type { TFunction } from 'i18next'

export interface ApiErrorBody {
  error: string
  error_args?: Record<string, unknown>
}

/**
 * Traduit un code d'erreur renvoyé par l'API. Un code sans traduction retombe
 * sur un message générique qui affiche le code, jamais une clef brute.
 */
export function translateError(t: TFunction, body: ApiErrorBody): string {
  const key = `errors.${body.error}`
  return t(key, {
    ...body.error_args,
    defaultValue: t('errors.generic', { code: body.error }),
  })
}
