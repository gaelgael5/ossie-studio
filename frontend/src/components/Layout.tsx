import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { LanguageSelector } from '../LanguageSelector'

export function Layout({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', maxWidth: 760, margin: '32px auto', padding: '0 16px' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <strong>{t('appName')}</strong>
        <LanguageSelector />
      </header>
      {children}
    </div>
  )
}
