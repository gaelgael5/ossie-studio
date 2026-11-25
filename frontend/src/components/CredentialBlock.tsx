import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { PersonalCredential } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from './ErrorList'

const STATUS_CLASS: Record<PersonalCredential['status'], string> = {
  a_saisir: 'text-warn',
  jamais_teste: 'text-warn',
  valide: 'text-ok',
  refuse: 'text-danger',
}

// Mon identifiant personnel pour la forge de l'espace : je ne saisis que mon
// jeton (forge et URL viennent de la connexion de l'espace), puis je le teste
// contre le droit d'écriture sur le dépôt. La valeur n'est jamais réaffichée.
export function CredentialBlock({ spaceId }: { spaceId: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [messages, setMessages] = useState<string[]>([])
  const key = ['my-credential', spaceId]
  const cred = useQuery({ queryKey: key, queryFn: () => api.myCredential(spaceId) })
  const update = (data: PersonalCredential) => queryClient.setQueryData(key, data)
  const onError = (err: unknown) => setMessages(errorMessages(t, err))

  const save = useMutation({
    mutationFn: (id: string) => api.setMyCredential(id, value),
    onSuccess: (data) => {
      update(data)
      setValue('')
      setEditing(false)
    },
    onError,
  })
  const test = useMutation({
    mutationFn: (id: string) => api.testMyCredential(id, spaceId),
    onSuccess: update,
    onError,
  })

  const data = cred.data
  if (!data) return null
  return (
    <div className="panel stack">
      <p className="panel-label">{t('credentialTitle', { url: data.url })}</p>
      <ErrorList messages={messages} />
      <div className="row">
        <span className={STATUS_CLASS[data.status]}>{t(`credentialStatus_${data.status}`)}</span>
        {data.forge_login ? <span className="muted">{t('credentialLogin', { login: data.forge_login })}</span> : null}
        {data.error_code ? (
          <span className="text-danger">{t(`errors.${data.error_code}`, { defaultValue: data.error_code })}</span>
        ) : null}
      </div>
      {editing ? (
        <form
          className="field-inline"
          onSubmit={(e) => {
            e.preventDefault()
            setMessages([])
            save.mutate(data.id)
          }}
        >
          <input
            className="input"
            type="password"
            autoComplete="off"
            aria-label={t('credentialValue')}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <button type="submit" className="btn btn-primary" disabled={!value.trim() || save.isPending}>
            {t('save')}
          </button>
        </form>
      ) : null}
      <div className="row">
        <button type="button" className="btn" onClick={() => setEditing((v) => !v)}>
          {t('credentialEnter')}
        </button>
        <button
          type="button"
          className="btn"
          disabled={data.status === 'a_saisir' || test.isPending}
          onClick={() => {
            setMessages([])
            test.mutate(data.id)
          }}
        >
          {t('credentialTest')}
        </button>
      </div>
    </div>
  )
}
