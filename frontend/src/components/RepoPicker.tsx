import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'

const SHOWN = 50

// Choisir le dépôt d'un espace dans la liste des dépôts lisibles par la
// connexion, filtrable, plutôt que de taper son URL. La saisie manuelle reste
// possible pour un dépôt absent de la liste (droits partiels, forge qui ne le
// liste pas).
export function RepoPicker(props: { connectionId: string; value: string; onChange: (url: string) => void }) {
  const { connectionId, value, onChange } = props
  const { t } = useTranslation()
  const [filter, setFilter] = useState('')
  const [manual, setManual] = useState(false)
  const repos = useQuery({
    queryKey: ['repos', connectionId],
    queryFn: () => api.listConnectionRepositories(connectionId),
    enabled: !!connectionId && !manual,
  })
  const all = repos.data?.repositories ?? []
  const needle = filter.trim().toLowerCase()
  const matching = all.filter((r) => r.full_name.toLowerCase().includes(needle))
  const chosen = all.find((r) => r.clone_url === value)

  if (manual) {
    return (
      <div className="field">
        <label className="field-label" htmlFor="repo">
          {t('repoUrlLabel')}
        </label>
        <input id="repo" className="input" value={value} onChange={(e) => onChange(e.target.value)} />
        <button type="button" className="btn-link" onClick={() => setManual(false)}>
          {t('repoPickFromList')}
        </button>
      </div>
    )
  }
  return (
    <div className="field">
      <label className="field-label" htmlFor="repo-filter">
        {t('repoPickLabel')}
      </label>
      <input
        id="repo-filter"
        className="input"
        placeholder={t('filterRepos')}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {repos.isError ? <span className="text-danger">{errorMessages(t, repos.error).join(' ')}</span> : null}
      {repos.isLoading ? <span className="muted">{t('loading')}</span> : null}
      {repos.data ? (
        <ul className="list-plain stack stack-tight" role="listbox" aria-label={t('repoPickLabel')}>
          {matching.slice(0, SHOWN).map((r) => (
            <li key={r.full_name}>
              <button
                type="button"
                role="option"
                aria-selected={r.clone_url === value}
                className={r.clone_url === value ? 'btn btn-sm btn-primary' : 'btn btn-sm'}
                onClick={() => onChange(r.clone_url)}
              >
                {r.full_name}
              </button>
            </li>
          ))}
          {matching.length === 0 ? <li className="muted">{t('repoNoMatch')}</li> : null}
          {matching.length > SHOWN ? (
            <li className="muted">{t('repoMoreRefine', { count: matching.length - SHOWN })}</li>
          ) : null}
        </ul>
      ) : null}
      {chosen ? <span className="field-hint mono">{chosen.clone_url}</span> : null}
      <button type="button" className="btn-link" onClick={() => setManual(true)}>
        {t('repoEnterManually')}
      </button>
    </div>
  )
}
