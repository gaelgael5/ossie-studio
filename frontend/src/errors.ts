import type { TFunction } from 'i18next'

export interface ApiErrorBody {
  error: string
  error_args?: Record<string, unknown>
}

export class ApiRequestError extends Error {
  body: ApiErrorBody
  status: number
  constructor(body: ApiErrorBody, status: number) {
    super(body.error)
    this.body = body
    this.status = status
  }
}

/**
 * Traduit un code d'erreur renvoyé par l'API. Un code sans traduction retombe
 * sur un message générique qui affiche le code, jamais une clef brute.
 */
export function translateError(t: TFunction, body: ApiErrorBody): string {
  return t(`errors.${body.error}`, {
    ...body.error_args,
    defaultValue: t('errors.generic', { code: body.error }),
  })
}

/**
 * Messages à afficher pour une erreur : la liste des erreurs de champ quand
 * l'API renvoie `invalid_request` avec des codes de champ, sinon le message du
 * code lui-même.
 */
export function errorMessages(t: TFunction, err: unknown): string[] {
  if (!(err instanceof ApiRequestError)) return [t('errors.generic', { code: 'unknown' })]
  const fields = err.body.error_args?.fields
  if (err.body.error === 'invalid_request' && Array.isArray(fields)) {
    return (fields as string[]).map((code) =>
      t(`fields.${code}`, { defaultValue: t('errors.generic', { code }) }),
    )
  }
  return [translateError(t, err.body)]
}
