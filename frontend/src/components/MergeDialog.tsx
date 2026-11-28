import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { MergeChoice, MergeConflict, MergeView } from '../apiClient'
import { errorMessages } from '../errors'
import { parseFree, show } from '../mergeUtils'
import { ErrorList } from './ErrorList'
import { PropertyDiff } from './PropertyDiff'

type Pick = 'theirs' | 'mine' | 'free'

// Fenêtre de fusion d'un objet du brouillon : la version publiée à gauche, la
// mienne à droite, en propriétés. Une propriété changée des deux côtés demande
// un choix (publiée / la mienne / saisie libre). Sans changement côté publié :
// lecture seule, pour voir la correspondance des modifications.
export function MergeDialog(props: { spaceId: string; branchId: string; path: string; onClose: () => void }) {
  const { spaceId, branchId, path, onClose } = props
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [picks, setPicks] = useState<Record<string, Pick>>({})
  const [free, setFree] = useState<Record<string, string>>({})
  const [messages, setMessages] = useState<string[]>([])
  const view = useQuery({ queryKey: ['merge', spaceId, branchId, path], queryFn: () => api.draftMerge(spaceId, branchId, path) })

  const save = useMutation({
    mutationFn: (body: { resolutions?: Record<string, MergeChoice>; delete?: boolean }) =>
      api.resolveMerge(spaceId, branchId, { path, ...body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries()
      onClose()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const data = view.data
  const choice = (pointer: string): MergeChoice | null => {
    const pick = picks[pointer]
    if (pick === 'theirs' || pick === 'mine') return pick
    if (pick === 'free') return { value: parseFree(free[pointer] ?? '') }
    return null
  }
  const pointers = data?.kind === 'text_conflict' ? [''] : (data?.conflicts ?? []).map((c) => c.pointer)
  const complete = pointers.every((p) => choice(p) !== null)
  const submit = () => {
    setMessages([])
    const resolutions: Record<string, MergeChoice> = {}
    for (const p of pointers) resolutions[p] = choice(p) as MergeChoice
    save.mutate({ resolutions })
  }

  return (
    <section role="dialog" aria-label={t('mergeTitle', { path })} className="panel panel-form stack">
      <div className="panel-head">
        <h3 className="panel-title">{t('mergeTitle', { path })}</h3>
        <button type="button" className="btn btn-sm" onClick={onClose}>
          {t('close')}
        </button>
      </div>
      <ErrorList messages={messages} />
      {data?.collected ? <p className="info-note">{t('mergeCollected')}</p> : null}
      {data ? <MergeBody data={data} picks={picks} free={free} setPick={(p, v) => setPicks({ ...picks, [p]: v })} setFree={(p, v) => setFree({ ...free, [p]: v })} /> : null}
      {data && !data.readonly ? (
        <div className="row">
          {data.kind === 'deleted_upstream' || data.kind === 'deleted_in_draft' ? (
            <>
              <button type="button" className="btn btn-primary" onClick={() => save.mutate({ delete: false })}>
                {t(data.kind === 'deleted_upstream' ? 'mergeKeepMine' : 'mergeKeepPublished')}
              </button>
              <button type="button" className="btn btn-danger" onClick={() => save.mutate({ delete: true })}>
                {t('mergeAcceptDeletion')}
              </button>
            </>
          ) : (
            <button type="button" className="btn btn-primary" disabled={!complete || save.isPending} onClick={submit}>
              {t('mergeValidate')}
            </button>
          )}
        </div>
      ) : null}
    </section>
  )
}

function MergeBody(props: {
  data: MergeView
  picks: Record<string, Pick>
  free: Record<string, string>
  setPick: (pointer: string, pick: Pick) => void
  setFree: (pointer: string, text: string) => void
}) {
  const { data } = props
  const { t } = useTranslation()
  if (data.kind === 'deleted_upstream' || data.kind === 'deleted_in_draft') {
    return <p>{t(data.kind === 'deleted_upstream' ? 'mergeDeletedUpstream' : 'mergeDeletedInDraft')}</p>
  }
  if (!data.structured) {
    return (
      <div className="stack">
        <div className="form-grid">
          {(['base', 'theirs', 'mine'] as const).map((side) => (
            <div key={side} className="field">
              <span className="field-label">{t(`mergeSide_${side}`)}</span>
              <pre className="mono">{show(data[side])}</pre>
            </div>
          ))}
        </div>
        {data.kind === 'text_conflict' && !data.collected ? <ConflictChoice pointer="" conflict={{ pointer: '', base: data.base, theirs: data.theirs, mine: data.mine }} {...props} /> : null}
      </div>
    )
  }
  return (
    <div className="stack">
      <p className="muted">{data.readonly ? t('mergeReadonly') : t(`mergeKind_${data.kind}`)}</p>
      <PropertyDiff left={data.theirs} right={data.mine} />
      {/* Objet collecté : les conflits se lisent dans l'écart, sans choix à faire. */}
      {data.collected
        ? null
        : data.conflicts.map((c) => <ConflictChoice key={c.pointer} pointer={c.pointer} conflict={c} {...props} />)}
    </div>
  )
}

function ConflictChoice(props: {
  pointer: string
  conflict: MergeConflict
  picks: Record<string, Pick>
  free: Record<string, string>
  setPick: (pointer: string, pick: Pick) => void
  setFree: (pointer: string, text: string) => void
}) {
  const { pointer, conflict, picks, free, setPick, setFree } = props
  const { t } = useTranslation()
  const name = `pick-${pointer || 'objet'}`
  return (
    <fieldset className="fieldset">
      <legend>{t('mergeConflictOn', { pointer: pointer || t('mergeWholeObject') })}</legend>
      <label className="check">
        <input type="radio" name={name} checked={picks[pointer] === 'theirs'} onChange={() => setPick(pointer, 'theirs')} />
        {t('mergePickTheirs', { value: show(conflict.theirs) })}
      </label>
      <label className="check">
        <input type="radio" name={name} checked={picks[pointer] === 'mine'} onChange={() => setPick(pointer, 'mine')} />
        {t('mergePickMine', { value: show(conflict.mine) })}
      </label>
      <label className="check">
        <input type="radio" name={name} checked={picks[pointer] === 'free'} onChange={() => setPick(pointer, 'free')} />
        {t('mergePickFree')}
      </label>
      {picks[pointer] === 'free' ? (
        <textarea
          className="textarea mono"
          aria-label={t('mergeFreeValue')}
          value={free[pointer] ?? ''}
          onChange={(e) => setFree(pointer, e.target.value)}
        />
      ) : null}
      <span className="field-hint">{t('mergeBaseWas', { value: show(conflict.base) })}</span>
    </fieldset>
  )
}
