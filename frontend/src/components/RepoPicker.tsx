import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { Combobox } from './Combobox'

// Choisir le dépôt d'un espace dans la liste des dépôts lisibles par la
// connexion (liste déroulante filtrable) plutôt que de taper son URL. La saisie
// manuelle reste possible pour un dépôt absent de la liste (droits partiels,
// forge qui ne le liste pas).
export function RepoPicker(props: { connectionId: string; value: string; onChange: (url: string) => void }) {
  const { connectionId, value, onChange } = props
  const { t } = useTranslation()
  const [manual, setManual] = useState(false)
  const repos = useQuery({
    queryKey: ['repos', connectionId],
    queryFn: () => api.listConnectionRepositories(connectionId),
    enabled: !!connectionId && !manual,
  })

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
  const status = repos.isError ? (
    <span className="text-danger">{errorMessages(t, repos.error).join(' ')}</span>
  ) : null
  return (
    <div className="field">
      <label className="field-label" htmlFor="repo-picker">
        {t('repoPickLabel')}
      </label>
      <Combobox
        id="repo-picker"
        options={(repos.data?.repositories ?? []).map((r) => ({ value: r.clone_url, label: r.full_name }))}
        value={value}
        onChange={onChange}
        placeholder={t('repoPickPlaceholder')}
        filterPlaceholder={t('filterRepos')}
        emptyText={t('repoNoMatch')}
        moreText={(count) => t('repoMoreRefine', { count })}
        status={status}
        busy={repos.isFetching}
        busyText={t('repoLoading')}
      />
      {value ? <span className="field-hint mono">{value}</span> : null}
      <button type="button" className="btn-link" onClick={() => setManual(true)}>
        {t('repoEnterManually')}
      </button>
    </div>
  )
}
