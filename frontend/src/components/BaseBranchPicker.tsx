import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from './BusyButton'
import { Combobox } from './Combobox'

// Branche de base d'un espace : d'abord CHOISIR parmi les branches existantes
// du dépôt ; « Créer une nouvelle branche » seulement si c'est le choix de
// l'utilisateur. Pas de slug ici : le nom git est l'identité visible. La racine
// (branche par défaut) n'est pas proposée : elle reste en lecture seule.
export function BaseBranchPicker({
  spaceId,
  onDone,
  onError,
}: {
  spaceId: string
  onDone: () => Promise<void> | void
  onError: (messages: string[]) => void
}) {
  const { t } = useTranslation()
  const [chosen, setChosen] = useState('')
  const [creating, setCreating] = useState(false)
  const [newRef, setNewRef] = useState('work/')

  const remote = useQuery({
    queryKey: ['remote-branches', spaceId],
    queryFn: () => api.listRemoteBranches(spaceId),
  })
  const save = useMutation({
    mutationFn: () =>
      api.createBaseBranch(spaceId, creating ? { new_ref: newRef.trim() } : { git_ref: chosen }),
    onSuccess: onDone,
    onError: (err) => onError(errorMessages(t, err)),
  })

  const root = remote.data?.root ?? ''
  const options = (remote.data?.branches ?? [])
    .filter((b) => !b.is_root && !b.tracked)
    .map((b) => ({ value: b.name, label: b.name }))

  return (
    <div className="stack">
      {creating ? (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault()
            onError([])
            save.mutate()
          }}
        >
          <div className="field">
            <label className="field-label" htmlFor="new-base">
              {t('newBaseNameLabel')}
            </label>
            <input id="new-base" className="input mono" value={newRef} onChange={(e) => setNewRef(e.target.value)} />
            <span className="field-hint">{t('newBaseHint', { root: root || '—' })}</span>
          </div>
          <div className="actions">
            <BusyButton
              type="submit"
              className="btn btn-primary"
              busy={save.isPending}
              busyText={t('branchCreating')}
              disabled={!newRef.trim() || newRef.trim().endsWith('/')}
            >
              {t('createBaseButton')}
            </BusyButton>
            <button type="button" className="btn-link" onClick={() => setCreating(false)}>
              {t('pickExistingBranch')}
            </button>
          </div>
        </form>
      ) : (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault()
            onError([])
            save.mutate()
          }}
        >
          <div className="field">
            <label className="field-label" htmlFor="base-picker">
              {t('baseBranchPickLabel')}
            </label>
            <Combobox
              id="base-picker"
              options={options}
              value={chosen}
              onChange={setChosen}
              placeholder={t('baseBranchPickPlaceholder')}
              filterPlaceholder={t('filterBranches')}
              emptyText={t('noOtherBranch')}
              moreText={(count) => t('repoMoreRefine', { count })}
              busy={remote.isFetching}
              busyText={t('branchesLoading')}
              status={
                remote.isError ? (
                  <span className="text-danger">{errorMessages(t, remote.error).join(' ')}</span>
                ) : null
              }
            />
            {root ? <span className="field-hint">{t('rootReadonlyHint', { root })}</span> : null}
          </div>
          <div className="actions">
            <BusyButton
              type="submit"
              className="btn btn-primary"
              busy={save.isPending}
              busyText={t('branchAdopting')}
              disabled={!chosen}
            >
              {t('adoptBaseButton')}
            </BusyButton>
            <button type="button" className="btn" onClick={() => setCreating(true)}>
              {t('createNewBranchButton')}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
