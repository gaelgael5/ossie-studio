import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { api, type SessionUser } from '../apiClient'

export function HomePage({ user }: { user: SessionUser }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const logout = useMutation({
    mutationFn: () => api.logout(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      navigate('/login')
    },
  })

  return (
    <main>
      <h1>{t('appName')}</h1>
      <p>{t('homeLoggedInAs', { email: user.email, role: user.role })}</p>
      {user.role === 'admin' && (
        <nav style={{ display: 'flex', gap: 16 }}>
          <Link to="/secrets">{t('manageSecrets')}</Link>
          <Link to="/oidc/connectors">{t('manageConnectors')}</Link>
        </nav>
      )}
      <p>{t('semanticSoon')}</p>
      <button type="button" onClick={() => logout.mutate()}>
        {t('logout')}
      </button>
    </main>
  )
}
