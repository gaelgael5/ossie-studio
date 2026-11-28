import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { Layout } from './components/Layout'
import { SUPPORTED_LANGUAGES } from './i18n'
import { useSession } from './hooks'
import { sessionCapabilities } from './apiClient'
import { SetupPage } from './pages/SetupPage'
import { LoginPage } from './pages/LoginPage'
import { HomePage } from './pages/HomePage'
import { SecretsPage } from './pages/SecretsPage'
import { ConnectorsPage } from './pages/ConnectorsPage'
import { ForgesPage } from './pages/ForgesPage'
import { SpacesPage } from './pages/SpacesPage'
import { SpaceDetailPage } from './pages/SpaceDetailPage'
import { PublishPage } from './pages/PublishPage'
import { GovernanceTagsPage } from './pages/GovernanceTagsPage'
import { GovernanceTagRulesPage } from './pages/GovernanceTagRulesPage'
import { GovernanceRelationProfilesPage } from './pages/GovernanceRelationProfilesPage'
import { GovernanceClassificationRulesPage } from './pages/GovernanceClassificationRulesPage'
import { GovernanceCertificationsPage } from './pages/GovernanceCertificationsPage'
import { GovernanceClassificationsPage } from './pages/GovernanceClassificationsPage'
import { GovernanceInvalidValuesPage } from './pages/GovernanceInvalidValuesPage'
import { SourcesPage } from './pages/SourcesPage'
import { CurationPage } from './pages/CurationPage'
import { HistoryPage } from './pages/HistoryPage'
import { ParametersPage } from './pages/ParametersPage'
import { DomainPage, DomainsPage } from './pages/DomainsPage'
import { CartographiePage } from './pages/CartographiePage'
import { CartoTypePage } from './cartographie/CartoTypePage'
import { CartoObjectPage } from './cartographie/CartoObjectPage'
import { CartoModelPage } from './cartographie/CartoModelPage'
import { CartoProcessPage } from './cartographie/CartoProcessPage'
import { ReferentielsPage } from './pages/ReferentielsPage'
import { SourceDetailPage } from './pages/SourceDetailPage'
import { ParsingPluginsPage } from './pages/ParsingPluginsPage'
import { WorkspaceProvider } from './workspace'
import { ProfilesPage } from './pages/ProfilesPage'
import { TeamsPage } from './pages/TeamsPage'
import { UsersPage } from './pages/UsersPage'
import { ApiKeysPage } from './pages/ApiKeysPage'
import { AgentProfilesPage } from './pages/AgentProfilesPage'
import { ConsentPage } from './pages/ConsentPage'
import { AgentConnectionsPage } from './pages/AgentConnectionsPage'
import { loginWithNext, safeNext } from './safeNext'
import { StatusPage } from './StatusPage'

/** Déjà connecté sur /login : on va là où la connexion devait ramener. */
/** Ancienne adresse de l'écran « Modifications » : la publication l'a absorbé. */
function ChangesToPublish() {
  const { pathname } = useLocation()
  return <Navigate to={pathname.replace(/\/changes$/, '/publish')} replace />
}

function AfterLogin() {
  const [params] = useSearchParams()
  return <Navigate to={safeNext(params.get('next')) ?? '/'} replace />
}

/** Page réservée aux personnes connectées, qui y revient après la connexion. */
function RequireLogin({ authenticated, children }: { authenticated: boolean; children: JSX.Element }) {
  const location = useLocation()
  return authenticated ? children : <Navigate to={loginWithNext(location.pathname + location.search)} replace />
}

export function App() {
  return (
    <WorkspaceProvider>
      <AppRoutes />
    </WorkspaceProvider>
  )
}

function AppRoutes() {
  const { t, i18n } = useTranslation()
  const session = useSession()

  // Une fois connecté, la langue du compte l'emporte et le navigateur la retient
  // (i18next écrit dans localStorage). Valeur inconnue → on ne force rien.
  const accountLanguage = session.data?.language
  const isAuthenticated = session.data?.authenticated
  useEffect(() => {
    if (
      isAuthenticated &&
      accountLanguage &&
      (SUPPORTED_LANGUAGES as readonly string[]).includes(accountLanguage) &&
      i18n.resolvedLanguage !== accountLanguage
    ) {
      void i18n.changeLanguage(accountLanguage)
    }
  }, [isAuthenticated, accountLanguage, i18n])

  if (session.isLoading || !session.data) {
    return (
      <Layout>
        <p>{t('loading')}</p>
      </Layout>
    )
  }

  const { bootstrap_required, authenticated, user } = session.data
  const caps = sessionCapabilities(session.data)
  // Un utilisateur authentifié mais sans la capacité est renvoyé à l'accueil ;
  // la vraie protection est côté serveur (403). Le routage ne fait que masquer.
  const gate = (capability: string, element: JSX.Element) =>
    !authenticated ? <Navigate to="/login" replace /> : caps.includes(capability) ? element : <Navigate to="/" replace />

  return (
    <Layout>
      <Routes>
        <Route path="/status" element={<StatusPage />} />
        <Route
          path="/setup"
          element={bootstrap_required ? <SetupPage /> : <Navigate to="/" replace />}
        />
        <Route
          path="/login"
          element={
            bootstrap_required ? (
              <Navigate to="/setup" replace />
            ) : authenticated ? (
              <AfterLogin />
            ) : (
              <LoginPage />
            )
          }
        />
        <Route
          path="/"
          element={
            bootstrap_required ? (
              <Navigate to="/setup" replace />
            ) : !authenticated || !user ? (
              <Navigate to="/login" replace />
            ) : (
              <HomePage user={user} capabilities={caps} />
            )
          }
        />
        <Route path="/sources" element={gate('sources:read', <SourcesPage />)} />
        <Route path="/sources/:slug" element={gate('sources:read', <SourceDetailPage />)} />
        <Route path="/curation" element={gate('curation:read', <CurationPage />)} />
        <Route path="/historique" element={gate('audit:read', <HistoryPage />)} />
        <Route path="/parametres" element={gate('settings:read', <ParametersPage />)} />
        <Route path="/referentiels" element={gate('referentiels:read', <ReferentielsPage />)} />
        <Route path="/referentiels/:name" element={gate('referentiels:read', <ReferentielsPage />)} />
        <Route path="/referentiels/:name/parametrage" element={gate('referentiels:read', <ReferentielsPage />)} />
        <Route path="/cartographie" element={<Navigate to="/cartographie/objets" replace />} />
        <Route path="/cartographie/types" element={gate('cartographie:read', <CartographiePage />)} />
        <Route path="/cartographie/types/:slug" element={gate('cartographie:read', <CartoTypePage />)} />
        <Route path="/cartographie/objets" element={gate('cartographie:read', <CartographiePage />)} />
        <Route path="/cartographie/objets/nouveau" element={gate('cartographie:read', <CartoObjectPage />)} />
        <Route path="/cartographie/objets/:type/:key" element={gate('cartographie:read', <CartoObjectPage />)} />
        <Route path="/cartographie/modeles" element={gate('cartographie:read', <CartographiePage />)} />
        <Route path="/cartographie/modeles/:slug" element={gate('cartographie:read', <CartoModelPage />)} />
        <Route path="/cartographie/processus" element={gate('cartographie:read', <CartographiePage />)} />
        <Route path="/cartographie/processus/:key" element={gate('cartographie:read', <CartoProcessPage />)} />
        <Route path="/domaines" element={gate('curation:read', <DomainsPage />)} />
        <Route path="/domaines/:name" element={gate('curation:read', <DomainPage />)} />
        <Route path="/secrets" element={gate('secrets:read', <SecretsPage />)} />
        <Route path="/oidc/connectors" element={gate('oidc:read', <ConnectorsPage />)} />
        <Route path="/forges" element={gate('connections:read', <ForgesPage />)} />
        <Route path="/parsing" element={gate('parsing:read', <ParsingPluginsPage />)} />
        <Route path="/spaces" element={gate('spaces:read', <SpacesPage />)} />
        <Route path="/spaces/:spaceId" element={gate('spaces:read', <SpaceDetailPage />)} />
        <Route
          path="/spaces/:spaceId/branches/:branchId/publish"
          element={gate('spaces:read', <PublishPage />)}
        />
        <Route
          path="/spaces/:spaceId/branches/:branchId/changes"
          // L'écran « Modifications » est entré dans la publication (ticket 173c6ed6).
          element={<ChangesToPublish />}
        />
        <Route path="/governance/tags" element={gate('governance:read', <GovernanceTagsPage />)} />
        <Route
          path="/governance/tag-rules"
          element={gate('governance:read', <GovernanceTagRulesPage />)}
        />
        <Route
          path="/governance/relation-profiles"
          element={gate('governance:read', <GovernanceRelationProfilesPage />)}
        />
        <Route
          path="/governance/classification-rules"
          element={gate('governance:read', <GovernanceClassificationRulesPage />)}
        />
        <Route
          path="/governance/certifications"
          element={gate('governance:read', <GovernanceCertificationsPage />)}
        />
        <Route
          path="/governance/classifications"
          element={gate('governance:read', <GovernanceClassificationsPage />)}
        />
        <Route
          path="/governance/invalid-values"
          element={gate('governance:read', <GovernanceInvalidValuesPage />)}
        />
        <Route path="/profiles" element={gate('profiles:read', <ProfilesPage />)} />
        <Route path="/teams" element={gate('users:read', <TeamsPage />)} />
        <Route path="/users" element={gate('users:read', <UsersPage />)} />
        <Route path="/agent-profiles" element={gate('agent_profiles:read', <AgentProfilesPage />)} />
        <Route path="/api-keys" element={gate('api_keys:read', <ApiKeysPage />)} />
        <Route
          path="/oauth/consent"
          element={
            bootstrap_required ? (
              <Navigate to="/setup" replace />
            ) : (
              <RequireLogin authenticated={Boolean(authenticated)}>
                <ConsentPage />
              </RequireLogin>
            )
          }
        />
        <Route
          path="/agent-connections"
          element={
            <RequireLogin authenticated={Boolean(authenticated)}>
              <AgentConnectionsPage
                canSeeAll={caps.includes('agent_connections:read')}
                canRevokeAll={caps.includes('agent_connections:write')}
              />
            </RequireLogin>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
