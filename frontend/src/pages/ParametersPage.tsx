import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, sessionCapabilities } from '../apiClient'
import type { AppSetting } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { useSession } from '../hooks'

// Page « Paramètres » (Réglages, ticket ae8fc4b6) : les paramètres généraux de
// l'application. Chacun a une valeur par défaut dans la configuration du serveur ;
// une valeur posée ici prime, sans redémarrage. Lecture : settings:read ;
// modification : settings:write.

const KB = 1024

export function ParametersPage() {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('settings:write')
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const settings = useQuery({ queryKey: ['app-settings'], queryFn: api.listSettings })

  return (
    <main>
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('parametersTitle')}</h1>
          <p className="page-sub">{t('parametersSubtitle')}</p>
        </div>
      </div>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}
      <div className="panel">
        <table className="table">
          <thead>
            <tr>
              <th>{t('colParam')}</th>
              <th>{t('colValue')}</th>
              <th aria-label={t('colActions')} />
            </tr>
          </thead>
          <tbody>
            {(settings.data?.settings ?? []).map((setting) => (
              <SizeRow
                key={setting.key}
                setting={setting}
                canWrite={canWrite}
                onSaved={(msg) => {
                  setMessages([])
                  setNotice(msg)
                }}
                onError={(msgs) => {
                  setNotice('')
                  setMessages(msgs)
                }}
              />
            ))}
          </tbody>
        </table>
      </div>
    </main>
  )
}

/** Un paramètre de taille, saisi en Ko (entier) et rangé en octets. */
function SizeRow({
  setting,
  canWrite,
  onSaved,
  onError,
}: {
  setting: AppSetting
  canWrite: boolean
  onSaved: (notice: string) => void
  onError: (messages: string[]) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const shown = String(Math.round(setting.value / KB))
  const [value, setValue] = useState(shown)
  useEffect(() => setValue(shown), [shown])
  const kb = Number(value)
  const valid =
    /^\d+$/.test(value.trim()) && kb * KB >= setting.minimum && kb * KB <= setting.maximum
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['app-settings'] })

  const save = useMutation({
    mutationFn: () => api.setSetting(setting.key, kb * KB),
    onSuccess: async () => {
      onSaved(t('parametersSaved'))
      await refresh()
    },
    onError: (err) => onError(errorMessages(t, err)),
  })
  const reset = useMutation({
    mutationFn: () => api.resetSetting(setting.key),
    onSuccess: async () => {
      onSaved(t('parametersReset'))
      await refresh()
    },
    onError: (err) => onError(errorMessages(t, err)),
  })
  const label = t(`parameter_${setting.key}`, { defaultValue: setting.key })

  return (
    <tr>
      <td>
        <strong>{label}</strong>
        <div className="muted small">{t(`parameterDoc_${setting.key}`, { defaultValue: '' })}</div>
        <div className="muted small">
          {t('parametersDefault', { value: Math.round(setting.default / KB) })} ·{' '}
          {setting.source === 'page'
            ? t('parametersFromPage', { who: setting.updated_by ?? '' })
            : t('parametersFromConfig')}
        </div>
      </td>
      <td>
        <div className="row" style={{ gap: 6, alignItems: 'center' }}>
          <input
            className="input mono"
            style={{ maxWidth: 120 }}
            aria-label={label}
            inputMode="numeric"
            value={value}
            readOnly={!canWrite}
            onChange={(e) => setValue(e.target.value)}
          />
          <span className="muted">{t('parametersUnitKb')}</span>
        </div>
        {!valid ? (
          <div className="text-danger small">
            {t('parametersRange', {
              minimum: Math.round(setting.minimum / KB),
              maximum: Math.round(setting.maximum / KB),
            })}
          </div>
        ) : null}
      </td>
      <td>
        {canWrite ? (
          <div className="row" style={{ gap: 6 }}>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={save.isPending || !valid || value === shown}
              onClick={() => save.mutate()}
            >
              {t('saveValue')}
            </button>
            {setting.source === 'page' ? (
              <button
                type="button"
                className="btn btn-sm"
                disabled={reset.isPending}
                onClick={() => reset.mutate()}
              >
                {t('parametersResetButton')}
              </button>
            ) : null}
          </div>
        ) : null}
      </td>
    </tr>
  )
}
