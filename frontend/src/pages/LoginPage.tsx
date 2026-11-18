import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages, translateError } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [messages, setMessages] = useState<string[]>([])

  const connectors = useQuery({ queryKey: ['publicConnectors'], queryFn: api.publicConnectors })

  const mutation = useMutation({
    mutationFn: () => api.login(email, password),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      navigate('/')
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  // Le flux OIDC revient ici avec un code d'erreur en cas d'échec.
  const oidcError = searchParams.get('error')
  const oidcMessages = oidcError ? [translateError(t, { error: oidcError })] : []

  return (
    <main>
      <h1>{t('loginTitle')}</h1>
      <ErrorList messages={[...messages, ...oidcMessages]} />
      {connectors.data && connectors.data.length > 0 && (
        <div style={{ maxWidth: 360 }}>
          {connectors.data.map((c) => (
            <a
              key={c.id}
              href={`/auth/oidc/${c.id}/login`}
              style={{ display: 'block', textAlign: 'center', padding: 10, border: '1px solid #2563eb', borderRadius: 8, marginBottom: 8 }}
            >
              {t('loginWith', { label: c.label })}
            </a>
          ))}
          <p style={{ textAlign: 'center', color: '#94a3b8' }}>{t('orLocalAccount')}</p>
        </div>
      )}
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
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={mutation.isPending}>
          {t('loginSubmit')}
        </button>
      </form>
    </main>
  )
}
