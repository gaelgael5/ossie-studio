import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { fetchHealth, type DbStatus, type HealthReport } from './api'

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
    <main className="panel stack" style={{ maxWidth: 560, margin: '40px auto 0' }}>
      <h1 className="page-title">{t('statusTitle')}</h1>
      <p className="muted">{t('today', { date: today })}</p>
      {isLoading && <p className="muted">{t('loading')}</p>}
      {isError && (
        <p role="alert" className="banner banner-danger">
          {t('unreachable')}
        </p>
      )}
      {data && (
        <>
          <p className={data.ok ? 'text-ok' : 'text-danger'}>
            <strong>{data.ok ? t('healthy') : t('degraded')}</strong>
          </p>
          <ul className="list-plain stack stack-tight">
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
