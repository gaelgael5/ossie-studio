import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages, translateError } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { safeNext } from '../safeNext'

export function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [messages, setMessages] = useState<string[]>([])

  const connectors = useQuery({ queryKey: ['publicConnectors'], queryFn: api.publicConnectors })
  // Où revenir une fois connecté (ex. l'écran de consentement d'un client MCP).
  const next = safeNext(searchParams.get('next'))

  const mutation = useMutation({
    mutationFn: () => api.login(email, password),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      navigate(next ?? '/')
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  // Le flux OIDC revient ici avec un code d'erreur en cas d'échec.
  const oidcError = searchParams.get('error')
  const oidcMessages = oidcError ? [translateError(t, { error: oidcError })] : []

  return (
    <main className="panel auth-card stack">
      <h1 className="page-title">{t('loginTitle')}</h1>
      <ErrorList messages={[...messages, ...oidcMessages]} />
      {connectors.data && connectors.data.length > 0 && (
        <>
          {connectors.data.map((c) => (
            <a
              key={c.id}
              href={`/auth/oidc/${c.id}/login${next ? `?next=${encodeURIComponent(next)}` : ''}`}
              className="btn btn-block"
            >
              {t('loginWith', { label: c.label })}
            </a>
          ))}
          <p className="auth-divider">{t('orLocalAccount')}</p>
        </>
      )}
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
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary btn-block" disabled={mutation.isPending}>
          {t('loginSubmit')}
        </button>
      </form>
    </main>
  )
}
