import { useTranslation } from 'react-i18next'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { useSession } from './hooks'
import { SetupPage } from './pages/SetupPage'
import { LoginPage } from './pages/LoginPage'
import { HomePage } from './pages/HomePage'
import { SecretsPage } from './pages/SecretsPage'
import { ConnectorsPage } from './pages/ConnectorsPage'
import { ForgesPage } from './pages/ForgesPage'
import { SpacesPage } from './pages/SpacesPage'
import { SpaceDetailPage } from './pages/SpaceDetailPage'
import { GovernanceTagsPage } from './pages/GovernanceTagsPage'
import { GovernanceCertificationsPage } from './pages/GovernanceCertificationsPage'
import { StatusPage } from './StatusPage'

export function App() {
  const { t } = useTranslation()
  const session = useSession()

  if (session.isLoading || !session.data) {
    return (
      <Layout>
        <p>{t('loading')}</p>
      </Layout>
    )
  }

  const { bootstrap_required, authenticated, user } = session.data
  const isAdmin = user?.role === 'admin'

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
              <Navigate to="/" replace />
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
              <HomePage user={user} />
            )
          }
        />
        <Route
          path="/secrets"
          element={isAdmin ? <SecretsPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/oidc/connectors"
          element={isAdmin ? <ConnectorsPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/forges"
          element={isAdmin ? <ForgesPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/spaces"
          element={isAdmin ? <SpacesPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/spaces/:spaceId"
          element={isAdmin ? <SpaceDetailPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/governance/tags"
          element={isAdmin ? <GovernanceTagsPage /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/governance/certifications"
          element={isAdmin ? <GovernanceCertificationsPage /> : <Navigate to="/login" replace />}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
