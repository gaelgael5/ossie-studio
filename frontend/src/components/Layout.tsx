import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../apiClient'
import { useSession } from '../hooks'
import { LanguageSelector } from '../LanguageSelector'
import { useWorkspace } from '../workspace'

/** Un item du menu principal. `to` absent = module pas encore construit. */
interface NavItem {
  key: string
  to?: string
  capability?: string
  prefix: string
}

// Menu principal, calqué sur les maquettes. Sources et Curation n'ont pas
// encore d'écran : ils restent visibles mais désactivés (honnête).
const MAIN_NAV: NavItem[] = [
  { key: 'navSpaces', to: '/spaces', capability: 'spaces:read', prefix: '/spaces' },
  { key: 'navSources', to: '/sources', capability: 'sources:read', prefix: '/sources' },
  { key: 'navCuration', prefix: '/curation' },
  { key: 'navGovernance', to: '/governance/tags', capability: 'governance:read', prefix: '/governance' },
]

// Regroupées sous « Réglages » (menu déroulant).
const SETTINGS_NAV: NavItem[] = [
  { key: 'navSecrets', to: '/secrets', capability: 'secrets:read', prefix: '/secrets' },
  { key: 'navConnectors', to: '/oidc/connectors', capability: 'oidc:read', prefix: '/oidc' },
  { key: 'navForges', to: '/forges', capability: 'connections:read', prefix: '/forges' },
  { key: 'navProfiles', to: '/profiles', capability: 'profiles:read', prefix: '/profiles' },
  { key: 'navUsers', to: '/users', capability: 'users:read', prefix: '/users' },
]

export function Layout({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const session = useSession()
  const authenticated = session.data?.authenticated ?? false
  const user = session.data?.user
  const caps = session.data?.capabilities ?? []

  if (!authenticated || !user) {
    return (
      <div className="app-shell">
        <div className="topbar-plain">
          <strong>{t('appName')}</strong>
          <LanguageSelector />
        </div>
        <div className="page">{children}</div>
      </div>
    )
  }

  const can = (capability?: string) => !capability || caps.includes(capability)

  return (
    <div className="app-shell">
      <AppNav userEmail={user.email} can={can} />
      <div className="page">{children}</div>
    </div>
  )
}

function WorkspaceChips() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  if (ws.spaces.length === 0) {
    return (
      <>
        <span className="ctx-chip" aria-disabled="true" title={t('navNoSpace')}>
          {t('navSpaceLabel')}
          {' : —'}
        </span>
        <span className="ctx-chip" aria-disabled="true" title={t('navNoSpace')}>
          {t('navBranchLabel')}
          {' : —'}
        </span>
      </>
    )
  }
  return (
    <>
      <select
        className="ctx-select"
        aria-label={t('navSpaceLabel')}
        value={ws.spaceId ?? ''}
        onChange={(e) => ws.setSpace(e.target.value)}
      >
        {ws.spaces.map((s) => (
          <option key={s.id} value={s.id}>
            {`${t('navSpaceLabel')} : ${s.name}`}
          </option>
        ))}
      </select>
      <select
        className="ctx-select"
        aria-label={t('navBranchLabel')}
        value={ws.branchId ?? ''}
        onChange={(e) => ws.setBranch(e.target.value)}
        disabled={ws.branches.length === 0}
      >
        {ws.branches.length === 0 ? <option value="">{`${t('navBranchLabel')} : —`}</option> : null}
        {ws.branches.map((b) => (
          <option key={b.id} value={b.id}>
            {`${t('navBranchLabel')} : ${b.label}`}
          </option>
        ))}
      </select>
    </>
  )
}

function AppNav({
  userEmail,
  can,
}: {
  userEmail: string
  can: (capability?: string) => boolean
}) {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [userOpen, setUserOpen] = useState(false)
  const settingsRef = useRef<HTMLDivElement>(null)
  const userRef = useRef<HTMLDivElement>(null)

  // Fermer les menus au changement de route.
  useEffect(() => {
    setSettingsOpen(false)
    setUserOpen(false)
  }, [location.pathname])

  // Fermer au clic en dehors.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) setSettingsOpen(false)
      if (userRef.current && !userRef.current.contains(e.target as Node)) setUserOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const logout = useMutation({
    mutationFn: () => api.logout(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
      navigate('/login')
    },
  })

  const isActive = (prefix: string) => location.pathname.startsWith(prefix)
  const settingsItems = SETTINGS_NAV.filter((item) => can(item.capability))
  const settingsActive = settingsItems.some((item) => isActive(item.prefix))

  return (
    <nav className="topnav" aria-label={t('appName')}>
      <span className="topnav-brand">{t('appName')}</span>
      <div className="topnav-menu">
        {MAIN_NAV.map((item) => {
          const label = t(item.key)
          if (!item.to) {
            return (
              <span
                key={item.key}
                className="topnav-item"
                aria-disabled="true"
                title={t('navNotReady')}
              >
                {label}
              </span>
            )
          }
          if (!can(item.capability)) return null
          return (
            <Link
              key={item.key}
              to={item.to}
              className={`topnav-item${isActive(item.prefix) ? ' active' : ''}`}
            >
              {label}
            </Link>
          )
        })}
        {settingsItems.length > 0 ? (
          <div className="topnav-dropdown" ref={settingsRef}>
            <button
              type="button"
              className={`topnav-item${settingsActive ? ' active' : ''}`}
              aria-haspopup="true"
              aria-expanded={settingsOpen}
              onClick={() => setSettingsOpen((v) => !v)}
            >
              {t('navSettings')}
              {' ▾'}
            </button>
            {settingsOpen ? (
              <div className="topnav-dropdown-menu" role="menu">
                {settingsItems.map((item) => (
                  <Link key={item.key} to={item.to as string} role="menuitem">
                    {t(item.key)}
                  </Link>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="topnav-right">
        <WorkspaceChips />
        <LanguageSelector />
        <div className="topnav-dropdown" ref={userRef}>
          <button
            type="button"
            className="topnav-user"
            style={{ background: 'none', border: 'none', cursor: 'pointer' }}
            aria-haspopup="true"
            aria-expanded={userOpen}
            onClick={() => setUserOpen((v) => !v)}
          >
            {userEmail}
            {' ▾'}
          </button>
          {userOpen ? (
            <div className="topnav-dropdown-menu" role="menu">
              <Link to="/" role="menuitem">
                {t('home')}
              </Link>
              <button
                type="button"
                className="btn btn-sm"
                style={{ margin: 6 }}
                onClick={() => logout.mutate()}
              >
                {t('logout')}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </nav>
  )
}
