import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function SetupPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [messages, setMessages] = useState<string[]>([])

  const mutation = useMutation({
    mutationFn: () => api.setup(email, password),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      navigate('/')
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  return (
    <main className="panel auth-card stack">
      <h1 className="page-title">{t('setupTitle')}</h1>
      <p className="muted">{t('setupIntro')}</p>
      <p className="muted">{t('setupEmailHint')}</p>
      <ErrorList messages={messages} />
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          mutation.mutate()
        }}
      >
        <div className="field">
          <label htmlFor="email" className="field-label">
            {t('emailLabel')}
          </label>
          <input
            id="email"
            type="email"
            className="input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="password" className="field-label">
            {t('passwordLabel')}
          </label>
          <input
            id="password"
            type="password"
            className="input"
            aria-describedby="password-hint"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <span id="password-hint" className="field-hint">
            {t('passwordHint')}
          </span>
        </div>
        <button type="submit" className="btn btn-primary btn-block" disabled={mutation.isPending}>
          {t('createAdmin')}
        </button>
      </form>
    </main>
  )
}
