import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { fetchHealth, type DbStatus, type HealthReport } from './api'
import { LanguageSelector } from './LanguageSelector'

function dbLine(t: ReturnType<typeof useTranslation>['t'], db: DbStatus): string {
  if (!db.reachable) return t('dbUnreachable')
  if (!db.up_to_date)
    return t('dbReachableOutdated', { version: db.schema_version, expected: db.expected_version })
  return t('dbReachableUpToDate', { version: db.schema_version })
}

export function StatusPage() {
  const { t, i18n } = useTranslation()
  const { data, isLoading, isError } = useQuery<HealthReport>({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 10000,
  })

  // Date formatée selon la langue courante (jamais une locale codée en dur).
  const today = new Intl.DateTimeFormat(i18n.resolvedLanguage).format(new Date())

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', maxWidth: 560, margin: '40px auto' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1>{t('statusTitle')}</h1>
        <LanguageSelector />
      </header>
      <p>{t('today', { date: today })}</p>
      {isLoading && <p>{t('loading')}</p>}
      {isError && <p role="alert">{t('unreachable')}</p>}
      {data && (
        <>
          <p style={{ fontWeight: 600, color: data.ok ? '#15803d' : '#b91c1c' }}>
            {data.ok ? t('healthy') : t('degraded')}
          </p>
          <ul>
            <li>{dbLine(t, data.database)}</li>
            {data.volumes.map((v) => (
              <li key={v.name}>
                {v.writable
                  ? t('volumeWritable', { name: v.name })
                  : t('volumeNotWritable', { name: v.name, detail: v.detail ?? '' })}
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  )
}
