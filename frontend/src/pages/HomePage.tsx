import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { api, type SessionUser } from '../apiClient'
import { PageHeader } from '../components/PageHeader'

const ISSUES_URL = 'https://github.com/gaelgael5/ossie-studio/issues'

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
    ['users:read', '/users', 'manageUsers'],
  ]
  const visible = links.filter(([capability]) => can(capability))

  return (
    <main>
      <PageHeader
        title={t('appName')}
        subtitle={t('homeLoggedInAs', { email: user.email, role: user.role })}
        actions={
          <button type="button" className="btn" onClick={() => logout.mutate()}>
            {t('logout')}
          </button>
        }
      />
      <div className="page-body">
        {visible.length > 0 ? (
          <nav className="row">
            {visible.map(([, to, key]) => (
              <Link key={to} to={to} className="btn">
                {t(key)}
              </Link>
            ))}
          </nav>
        ) : (
          <div className="empty-note">{t('noAccessYet')}</div>
        )}
        <p className="muted">{t('semanticSoon')}</p>
        {/* Bugs et évolutions se suivent dans les issues GitHub du projet (ticket
            6fcefecf) ; l'adresse est affichée en clair pour pouvoir la recopier. */}
        <section className="panel stack home-feedback" aria-label={t('homeFeedbackTitle')}>
          <h2>{t('homeFeedbackTitle')}</h2>
          <p>{t('homeFeedbackText')}</p>
          <a href={ISSUES_URL} target="_blank" rel="noopener noreferrer" className="link-strong mono">
            {ISSUES_URL}
          </a>
        </section>
      </div>
    </main>
  )
}
