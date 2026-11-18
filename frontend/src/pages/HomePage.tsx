import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { api, type SessionUser } from '../apiClient'

export function HomePage({
  user,
  capabilities,
}: {
  user: SessionUser
  capabilities: string[]
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const can = (capability: string) => capabilities.includes(capability)

  const logout = useMutation({
    mutationFn: () => api.logout(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      navigate('/login')
    },
  })

  const links: [string, string, string][] = [
    ['secrets:read', '/secrets', 'manageSecrets'],
    ['oidc:read', '/oidc/connectors', 'manageConnectors'],
    ['connections:read', '/forges', 'manageForges'],
    ['spaces:read', '/spaces', 'manageSpaces'],
    ['governance:read', '/governance/tags', 'manageGovernanceTags'],
    ['governance:read', '/governance/certifications', 'manageCertifications'],
    ['governance:read', '/governance/classifications', 'manageClassifications'],
    ['profiles:read', '/profiles', 'manageProfiles'],
  ]
  const visible = links.filter(([capability]) => can(capability))

  return (
    <main>
      <h1>{t('appName')}</h1>
      <p>{t('homeLoggedInAs', { email: user.email, role: user.role })}</p>
      {visible.length > 0 ? (
        <nav style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          {visible.map(([, to, key]) => (
            <Link key={to} to={to}>
              {t(key)}
            </Link>
          ))}
        </nav>
      ) : (
        <p>{t('noAccessYet')}</p>
      )}
      <p>{t('semanticSoon')}</p>
      <button type="button" onClick={() => logout.mutate()}>
        {t('logout')}
      </button>
    </main>
  )
}
