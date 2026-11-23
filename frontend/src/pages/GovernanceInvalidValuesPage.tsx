import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { GovernanceFrame } from '../components/GovernanceFrame'

/**
 * Onglet « Valeurs invalides » (ticket docflow 5e8680d7, critère 6) : les
 * valeurs de classification portées par des assets et qui ne désignent aucun
 * niveau existant (niveau supprimé ou renommé, schéma supprimé, saisie à la
 * main). Elles n'affichent aucun badge ; on les corrige depuis la Curation.
 */
export function GovernanceInvalidValuesPage() {
  const { t } = useTranslation()
  const invalid = useQuery({
    queryKey: ['invalidClassifications'],
    queryFn: api.listInvalidClassifications,
  })
  const values = invalid.data?.values ?? []
  return (
    <GovernanceFrame active="invalid" subtitle={t('invalidValuesIntro')}>
      <ErrorList messages={invalid.error ? errorMessages(t, invalid.error) : []} />
      {invalid.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {invalid.data && values.length === 0 ? (
        <div className="empty-note">{t('invalidValuesNone')}</div>
      ) : null}
      {values.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('invalidColAsset')}</th>
                <th>{t('invalidColField')}</th>
                <th>{t('invalidColScheme')}</th>
                <th>{t('invalidColValue')}</th>
                <th>{t('invalidColReason')}</th>
              </tr>
            </thead>
            <tbody>
              {values.map((v) => (
                <tr key={`${v.source}/${v.path}/${v.field}/${v.scheme}/${v.value}`}>
                  <td>
                    <Link
                      to={`/curation?${new URLSearchParams({
                        source: v.source,
                        path: v.path,
                        tab: 'governance',
                      }).toString()}`}
                    >
                      {v.title}
                    </Link>{' '}
                    <span className="muted small">{v.source}</span>
                  </td>
                  <td className="mono">{v.field || t('invalidObject')}</td>
                  <td className="mono">{v.scheme}</td>
                  <td>{v.value}</td>
                  <td>{t(`invalidReason_${v.reason}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </GovernanceFrame>
  )
}
