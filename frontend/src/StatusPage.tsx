import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { fetchHealth, type HealthReport } from './api'

export function StatusPage() {
  const { t } = useTranslation()
  const { data, isLoading, isError } = useQuery<HealthReport>({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 10000,
  })

  if (isLoading) return <p>{t('loading')}</p>
  if (isError || !data) return <p role="alert">{t('error')}</p>

  const db = data.database
  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', maxWidth: 560, margin: '40px auto' }}>
      <h1>{t('title')}</h1>
      <p
        style={{
          fontWeight: 600,
          color: data.ok ? '#15803d' : '#b91c1c',
        }}
      >
        {data.ok ? t('healthy') : t('degraded')}
      </p>
      <ul>
        <li>
          {t('database')} : {db.reachable ? t('reachable') : t('unreachable')} —{' '}
          {db.up_to_date
            ? t('upToDate', { version: db.schema_version })
            : t('outOfDate')}
        </li>
        <li>
          {t('volumes')} :
          <ul>
            {data.volumes.map((v) => (
              <li key={v.name}>
                {v.name} : {v.writable ? t('writable') : t('notWritable')}
                {v.detail ? ` (${v.detail})` : ''}
              </li>
            ))}
          </ul>
        </li>
      </ul>
    </main>
  )
}
