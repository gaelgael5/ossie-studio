import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { parse as parseYaml } from 'yaml'
import { api } from '../apiClient'
import type { MergeChoice, MergeView } from '../apiClient'
import { errorMessages } from '../errors'
import { type At, editText, getAt, mergeBlocks, readEdit, sameAt, setAt, show } from '../mergeUtils'
import { ErrorList } from './ErrorList'
import { Icon } from './IconButton'
import { PropertyDiff } from './PropertyDiff'

// Fenêtre de fusion d'un objet du brouillon, par-dessus la page et presque plein
// écran (bug d45bbc68, mise à jour du 28/11 : « prendre le max de place »).
//
// Feature d727680a (Gael, 28/11 : « mettre la fusion au milieu » ; « un petit bouton
// sur les blocs à fusionner […] une flèche qui va vers la droite […] une flèche qui
// pointe vers la gauche ») : trois colonnes, version publiée | résultat | brouillon.
// Chaque bloc qui diffère porte une flèche de chaque côté ; plus de gros boutons de
// choix. Feature edaddc6d (« rends la colonne du milieu modifiable directement ») :
// le résultat se retouche sur place, les flèches continuent de marcher, leur état
// se déduit de la valeur en place.
//
// Un bloc est une propriété pour un objet structuré (règle de la fusion par
// propriétés, jamais ligne à ligne) ; pour un fichier de structure inconnue, le
// fichier entier est un seul bloc, modifiable. Objet raw : deux colonnes en lecture
// seule (ticket 173c6ed6). Sans changement côté publié : lecture seule.

type Body = { resolutions?: Record<string, MergeChoice>; delete?: boolean }

export function MergeDialog(props: { spaceId: string; branchId: string; path: string; onClose: () => void }) {
  const { spaceId, branchId, path, onClose } = props
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const view = useQuery({ queryKey: ['merge', spaceId, branchId, path], queryFn: () => api.draftMerge(spaceId, branchId, path) })
  const save = useMutation({
    mutationFn: (body: Body) => api.resolveMerge(spaceId, branchId, { path, ...body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries()
      onClose()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  // Échap referme, comme « Fermer » ; rien n'est enregistré sans « Valider ».
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const data = view.data
  const submit = (body: Body) => {
    setMessages([])
    save.mutate(body)
  }
  return (
    <div
      className="dialog-backdrop merge-backdrop"
      // Un clic dans la marge autour de la fenêtre la referme, comme Échap.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <section role="dialog" aria-modal="true" aria-label={t('mergeTitle', { path })} className="panel panel-form stack dialog-panel dialog-wide merge-dialog">
        <div className="panel-head">
          <h3 className="panel-title">{t('mergeTitle', { path })}</h3>
          <button type="button" className="btn btn-sm" onClick={onClose}>
            {t('close')}
          </button>
        </div>
        <ErrorList messages={[...messages, ...(view.error ? errorMessages(t, view.error) : [])]} />
        {/* Sur l'écran de publication, la fusion peut attendre les calculs de la page
            (validation, impacts) : le panneau le dit au lieu de rester blanc. */}
        {view.isPending ? (
          <p className="muted" aria-busy="true">
            <span className="spinner" aria-hidden="true" /> {t('loading')}
          </p>
        ) : null}
        {data ? <MergeContent data={data} path={path} busy={save.isPending} onSubmit={submit} onCancel={onClose} /> : null}
      </section>
    </div>
  )
}

function MergeContent({
  data,
  path,
  busy,
  onSubmit,
  onCancel,
}: {
  data: MergeView
  path: string
  busy: boolean
  onSubmit: (body: Body) => void
  onCancel: () => void
}) {
  if (data.collected) return <ReadOnlyTwoColumns data={data} collected />
  if (data.readonly) return <ReadOnlyTwoColumns data={data} />
  if (data.kind === 'deleted_upstream' || data.kind === 'deleted_in_draft') {
    return <DeletionMerge data={data} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
  }
  if (data.structured && (data.kind === 'auto' || data.kind === 'conflict')) {
    return <StructuredMerge data={data} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
  }
  return <TextMerge data={data} path={path} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
}

/** Contenu binaire (classeur d'exemple d'un import…) : pas d'aperçu lisible. */
function isBinary(value: unknown): boolean {
  return typeof value === 'string' && /[\u0000-\u0008\u000e-\u001f]/.test(value)
}

function Text({ value }: { value: unknown }) {
  const { t } = useTranslation()
  return isBinary(value) ? <p className="muted">{t('mergeBinary')}</p> : <pre className="mono merge-text">{show(value)}</pre>
}

/** Lecture seule, deux colonnes : objet raw (critère 13), ou rien de changé côté
 * publié, ou fichier créé (critère 10) — aucune flèche. */
function ReadOnlyTwoColumns({ data, collected = false }: { data: MergeView; collected?: boolean }) {
  const { t } = useTranslation()
  const note = collected
    ? t('mergeCollected')
    : data.state === 'created' && data.theirs_absent
      ? t('mergeCreated')
      : t('mergeReadonly')
  return (
    <div className="stack">
      <p className={collected ? 'info-note' : 'muted'}>{note}</p>
      {data.structured ? (
        <PropertyDiff left={data.theirs} right={data.mine} />
      ) : (
        <div className="merge-two">
          <div className="stack stack-tight">
            <span className="field-label">{t('mergeSide_theirs')}</span>
            <Text value={data.theirs} />
          </div>
          <div className="stack stack-tight">
            <span className="field-label">{t('mergeSide_mine')}</span>
            <Text value={data.mine} />
          </div>
        </div>
      )}
    </div>
  )
}

function Arrow({ direction, applied, label, onClick }: { direction: 'right' | 'left'; applied: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`btn btn-sm merge-arrow${applied ? ' merge-arrow-applied' : ''}`}
      aria-label={label}
      title={label}
      aria-pressed={applied}
      onClick={onClick}
    >
      <Icon name={direction === 'right' ? 'right' : 'left'} />
    </button>
  )
}

function Footer({ unresolved, valid, busy, onValidate, onCancel }: { unresolved: number; valid: boolean; busy: boolean; onValidate: () => void; onCancel: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="row merge-footer">
      <button type="button" className="btn" onClick={onCancel}>
        {t('cancelEdit')}
      </button>
      <button type="button" className="btn btn-primary" disabled={unresolved > 0 || !valid || busy} onClick={onValidate}>
        {t('mergeValidate')}
      </button>
    </div>
  )
}

function Status({ unresolved }: { unresolved: number }) {
  const { t } = useTranslation()
  return (
    <p className={unresolved > 0 ? 'merge-status text-warn' : 'merge-status text-ok'} role="status">
      {unresolved > 0 ? t('mergeRemaining', { count: unresolved }) : t('mergeResolved')}
    </p>
  )
}

function Head() {
  const { t } = useTranslation()
  return (
    <div className="merge-row merge-head">
      <span>{t('mergeColPublished')}</span>
      <span>{t('mergeColResult')}</span>
      <span>{t('mergeColDraft')}</span>
    </div>
  )
}

/** Objet structuré : un bloc par propriété. */
function StructuredMerge({ data, busy, onSubmit, onCancel }: { data: MergeView; busy: boolean; onSubmit: (body: Body) => void; onCancel: () => void }) {
  const { t } = useTranslation()
  const conflicts = useMemo(() => data.conflicts.map((c) => c.pointer), [data.conflicts])
  const initial = data.proposed ?? data.mine
  const [result, setResult] = useState<unknown>(initial)
  // Blocs tranchés par l'utilisateur (flèche ou retouche) : un conflit l'est dès lors.
  const [touched, setTouched] = useState<Set<string>>(new Set())
  // Saisie en cours d'un bloc retouché, et erreur de type éventuelle.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const blocks = useMemo(
    () =>
      mergeBlocks(conflicts, data.theirs, data.mine, initial).filter((p) => {
        const theirs = getAt(data.theirs, p)
        const mine = getAt(data.mine, p)
        return conflicts.includes(p) || !sameAt(theirs, mine)
      }),
    [conflicts, data.theirs, data.mine, initial],
  )
  const put = (pointer: string, at: At) => {
    setResult((prev: unknown) => setAt(prev, pointer, at))
    setTouched((prev) => new Set(prev).add(pointer))
  }
  const unresolved = conflicts.filter((p) => !touched.has(p)).length
  const take = (pointer: string, side: 'theirs' | 'mine') => {
    const own = getAt(side === 'theirs' ? data.theirs : data.mine, pointer)
    const other = getAt(side === 'theirs' ? data.mine : data.theirs, pointer)
    const current = getAt(result, pointer)
    const applied = touched.has(pointer) || !conflicts.includes(pointer) ? sameAt(current, own) : false
    setDrafts(({ [pointer]: _gone, ...rest }) => rest)
    setErrors(({ [pointer]: _gone, ...rest }) => rest)
    // Critère 5 : bloc ajouté d'un seul côté, flèche déjà appliquée → retiré ; un
    // autre clic le remet.
    if (applied && own.present && !other.present) put(pointer, { present: false, value: undefined })
    else put(pointer, own)
  }
  const edit = (pointer: string, text: string) => {
    setDrafts((prev) => ({ ...prev, [pointer]: text }))
    const read = readEdit(text, [getAt(data.theirs, pointer).value, getAt(data.mine, pointer).value])
    if (read.error) {
      setErrors((prev) => ({ ...prev, [pointer]: t(`mergeEditError_${read.error}`) }))
      setTouched((prev) => new Set(prev).add(pointer))
      return
    }
    setErrors(({ [pointer]: _gone, ...rest }) => rest)
    put(pointer, { present: true, value: read.value })
  }
  const validate = () => {
    const resolutions: Record<string, MergeChoice | { remove: true }> = {}
    for (const pointer of new Set([...conflicts, ...touched])) {
      const at = getAt(result, pointer)
      if (!conflicts.includes(pointer) && sameAt(at, getAt(initial, pointer))) continue
      resolutions[pointer] = at.present ? { value: at.value } : { remove: true }
    }
    onSubmit({ resolutions: resolutions as Record<string, MergeChoice> })
  }
  const hidden = mergeBlocks(conflicts, data.theirs, data.mine, initial).length - blocks.length
  return (
    <div className="stack">
      <Status unresolved={unresolved} />
      <div className="merge-grid" role="table" aria-label={t('mergeGridLabel')}>
        <Head />
        {blocks.map((pointer) => {
          const theirs = getAt(data.theirs, pointer)
          const mine = getAt(data.mine, pointer)
          const current = getAt(result, pointer)
          const pending = conflicts.includes(pointer) && !touched.has(pointer)
          const leftApplied = !pending && sameAt(current, theirs)
          const rightApplied = !pending && sameAt(current, mine)
          const origin = pending
            ? t('mergeOriginPending')
            : leftApplied && rightApplied
              ? ''
              : leftApplied
                ? t('mergeOriginTheirs')
                : rightApplied
                  ? t('mergeOriginMine')
                  : !current.present
                    ? t('mergeOriginRemoved')
                    : t('mergeOriginEdited')
          const name = pointer || t('mergeWholeObject')
          return (
            <div key={pointer} className={`merge-row${pending ? ' merge-row-pending' : ''}`} role="row">
              <div className="merge-cell merge-side" role="cell">
                <code className="merge-pointer">{name}</code>
                <span className="mono">{theirs.present ? show(theirs.value) : t('mergeAbsent')}</span>
                <Arrow direction="right" applied={leftApplied} label={t('mergeTakeTheirs', { name })} onClick={() => take(pointer, 'theirs')} />
              </div>
              <div className="merge-cell merge-result" role="cell">
                <input
                  className={`input mono${errors[pointer] ? ' input-invalid' : ''}`}
                  aria-label={t('mergeResultFor', { name })}
                  aria-invalid={Boolean(errors[pointer])}
                  value={drafts[pointer] ?? (pending ? '' : editText(current))}
                  placeholder={pending ? t('mergeOriginPending') : !current.present ? t('mergeAbsent') : ''}
                  onChange={(e) => edit(pointer, e.target.value)}
                />
                {errors[pointer] ? <span className="text-danger small">{errors[pointer]}</span> : null}
                {origin ? <span className={pending ? 'badge-warn' : 'muted small'}>{origin}</span> : null}
              </div>
              <div className="merge-cell merge-side" role="cell">
                <Arrow direction="left" applied={rightApplied} label={t('mergeTakeMine', { name })} onClick={() => take(pointer, 'mine')} />
                <span className="mono">{mine.present ? show(mine.value) : t('mergeAbsent')}</span>
              </div>
            </div>
          )
        })}
      </div>
      {hidden > 0 ? <p className="muted small">{t('mergeSameHidden', { count: hidden })}</p> : null}
      <Footer unresolved={unresolved} valid={Object.keys(errors).length === 0} busy={busy} onValidate={validate} onCancel={onCancel} />
    </div>
  )
}

/** Objet supprimé d'un côté (critère 6) : la flèche du côté où il est absent veut
 * dire « accepter la suppression », celle de l'autre côté « garder ». */
function DeletionMerge({ data, busy, onSubmit, onCancel }: { data: MergeView; busy: boolean; onSubmit: (body: Body) => void; onCancel: () => void }) {
  const { t } = useTranslation()
  const [side, setSide] = useState<'theirs' | 'mine' | null>(null)
  const upstream = data.kind === 'deleted_upstream' // absent côté publié
  const deletes = (s: 'theirs' | 'mine') => (upstream ? s === 'theirs' : s === 'mine')
  const label = (s: 'theirs' | 'mine') => (deletes(s) ? t('mergeAcceptDeletion') : s === 'theirs' ? t('mergeKeepPublished') : t('mergeKeepMine'))
  const shown = side === null ? null : side === 'theirs' ? data.theirs : data.mine
  return (
    <div className="stack">
      <p>{t(upstream ? 'mergeDeletedUpstream' : 'mergeDeletedInDraft')}</p>
      <Status unresolved={side === null ? 1 : 0} />
      <div className="merge-grid" role="table" aria-label={t('mergeGridLabel')}>
        <Head />
        <div className={`merge-row${side === null ? ' merge-row-pending' : ''}`} role="row">
          <div className="merge-cell merge-side" role="cell">
            {data.theirs_absent ? <span className="muted">{t('mergeAbsent')}</span> : <Text value={show(data.theirs)} />}
            <Arrow direction="right" applied={side === 'theirs'} label={label('theirs')} onClick={() => setSide('theirs')} />
          </div>
          <div className="merge-cell merge-result" role="cell">
            {side === null ? (
              <span className="badge-warn">{t('mergeOriginPending')}</span>
            ) : deletes(side) ? (
              <span className="muted">{t('mergeOriginRemoved')}</span>
            ) : (
              <Text value={show(shown)} />
            )}
          </div>
          <div className="merge-cell merge-side" role="cell">
            <Arrow direction="left" applied={side === 'mine'} label={label('mine')} onClick={() => setSide('mine')} />
            {data.mine_absent ? <span className="muted">{t('mergeAbsent')}</span> : <Text value={show(data.mine)} />}
          </div>
        </div>
      </div>
      <Footer unresolved={side === null ? 1 : 0} valid busy={busy} onValidate={() => side && onSubmit({ delete: deletes(side) })} onCancel={onCancel} />
    </div>
  )
}

/** Fichier de structure inconnue (repli texte) : le fichier entier est un bloc,
 * modifiable au milieu ; un YAML ou un JSON cassé ne se valide pas. */
function TextMerge({ data, path, busy, onSubmit, onCancel }: { data: MergeView; path: string; busy: boolean; onSubmit: (body: Body) => void; onCancel: () => void }) {
  const { t } = useTranslation()
  const theirs = typeof data.theirs === 'string' ? data.theirs : show(data.theirs)
  const mine = typeof data.mine === 'string' ? data.mine : show(data.mine)
  const conflict = data.kind === 'text_conflict'
  const [text, setText] = useState(typeof data.proposed === 'string' ? data.proposed : mine)
  const [touched, setTouched] = useState(false)
  const pending = conflict && !touched
  const error = touched ? unreadable(path, text) : null
  const choose = (value: string) => {
    setText(value)
    setTouched(true)
  }
  return (
    <div className="stack">
      <p className="muted">{t(`mergeKind_${data.kind}`)}</p>
      <Status unresolved={pending ? 1 : 0} />
      <div className="merge-grid" role="table" aria-label={t('mergeGridLabel')}>
        <Head />
        <div className={`merge-row merge-row-text${pending ? ' merge-row-pending' : ''}`} role="row">
          <div className="merge-cell merge-side" role="cell">
            <Text value={theirs} />
            <Arrow direction="right" applied={!pending && text === theirs} label={t('mergeTakeTheirs', { name: t('mergeWholeObject') })} onClick={() => choose(theirs)} />
          </div>
          <div className="merge-cell merge-result" role="cell">
            <textarea
              className={`textarea mono merge-textarea${error ? ' input-invalid' : ''}`}
              aria-label={t('mergeResultFor', { name: t('mergeWholeObject') })}
              aria-invalid={Boolean(error)}
              value={pending ? '' : text}
              placeholder={pending ? t('mergeOriginPending') : ''}
              onChange={(e) => choose(e.target.value)}
            />
            {error ? <span className="text-danger small">{error}</span> : null}
            {!pending && text !== theirs && text !== mine && touched ? <span className="muted small">{t('mergeOriginEdited')}</span> : null}
          </div>
          <div className="merge-cell merge-side" role="cell">
            <Arrow direction="left" applied={!pending && text === mine} label={t('mergeTakeMine', { name: t('mergeWholeObject') })} onClick={() => choose(mine)} />
            <Text value={mine} />
          </div>
        </div>
      </div>
      <Footer
        unresolved={pending ? 1 : 0}
        valid={!error}
        busy={busy}
        onValidate={() => onSubmit(touched ? { resolutions: { '': { value: text } } } : {})}
        onCancel={onCancel}
      />
    </div>
  )
}

/** Message d'erreur si le texte n'est plus un YAML ou un JSON lisible, sinon null. */
function unreadable(path: string, text: string): string | null {
  try {
    if (path.endsWith('.json')) JSON.parse(text)
    else if (path.endsWith('.yaml') || path.endsWith('.yml')) parseYaml(text)
    return null
  } catch (exc) {
    return exc instanceof Error ? exc.message.split('\n')[0] : String(exc)
  }
}
