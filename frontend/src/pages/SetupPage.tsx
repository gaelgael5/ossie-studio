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
    <main>
      <h1>{t('setupTitle')}</h1>
      <p>{t('setupIntro')}</p>
      <p>{t('setupEmailHint')}</p>
      <ErrorList messages={messages} />
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setMessages([])
          mutation.mutate()
        }}
        style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 360 }}
      >
        <label htmlFor="email">{t('emailLabel')}</label>
        <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <label htmlFor="password">{t('passwordLabel')}</label>
        <small>{t('passwordHint')}</small>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={mutation.isPending}>
          {t('createAdmin')}
        </button>
      </form>
    </main>
  )
}
