import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { parse as parseYaml } from 'yaml'
import { api } from '../apiClient'
import type { MergeChoice, MergeView } from '../apiClient'
import { errorMessages } from '../errors'
import { show } from '../mergeUtils'
import { CodeMerge } from './CodeMerge'
import { ErrorList } from './ErrorList'
import { Icon } from './IconButton'

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
// Feature fcf32f4a (maquette du 28/11, « je tiens au composant graphique ») : un
// vrai composant de fusion (`CodeMerge`, CodeMirror 5) — fichiers entiers de chaque
// côté, liaisons entre les blocs, au milieu le document entier modifiable. Le
// calcul reste celui du serveur, par propriétés et par clé dans les listes (jamais
// ligne à ligne) : il fournit le texte du milieu, chaque conflit marqué. Les flèches
// du composant ne font que recopier un bloc. Objet raw : deux panneaux en lecture
// seule (ticket 173c6ed6). Sans changement côté publié : lecture seule.

type Body = { resolutions?: Record<string, MergeChoice>; delete?: boolean; content?: string }

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
  const { t } = useTranslation()
  if ([data.texts.theirs, data.texts.mine].some(isBinary)) return <p className="muted">{t('mergeBinary')}</p>
  if (data.collected) return <ReadOnlyPanes data={data} path={path} collected />
  if (data.readonly) return <ReadOnlyPanes data={data} path={path} />
  if (data.kind === 'deleted_upstream' || data.kind === 'deleted_in_draft') {
    return <DeletionMerge data={data} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
  }
  return <GraphicalMerge data={data} path={path} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
}

/** Contenu binaire (classeur d'exemple d'un import…) : pas d'aperçu lisible. */
function isBinary(value: unknown): boolean {
  // Les caractères de contrôle sont précisément ce qui trahit un binaire.
  // eslint-disable-next-line no-control-regex
  return typeof value === 'string' && /[\u0000-\u0008\u000e-\u001f]/.test(value)
}

function Text({ value }: { value: unknown }) {
  const { t } = useTranslation()
  return isBinary(value) ? <p className="muted">{t('mergeBinary')}</p> : <pre className="mono merge-text">{show(value)}</pre>
}

/** Titres au-dessus des panneaux, alignés sur ceux du composant. */
function PaneTitles({ titles }: { titles: string[] }) {
  return (
    <div className={`code-merge-titles code-merge-titles-${titles.length}`}>
      {titles.map((title, index) => (
        <span key={title} className={index === 1 && titles.length === 3 ? 'code-merge-title-result' : undefined}>
          {title}
        </span>
      ))}
    </div>
  )
}

/** Lecture seule : objet raw (critère 13 de d727680a), rien de changé côté publié,
 * ou fichier créé (un seul panneau) — aucune flèche. */
function ReadOnlyPanes({ data, path, collected = false }: { data: MergeView; path: string; collected?: boolean }) {
  const { t } = useTranslation()
  const created = data.state === 'created' && data.theirs_absent
  const note = collected ? t('mergeCollected') : created ? t('mergeCreated') : t('mergeReadonly')
  const published = created ? null : (data.texts.theirs ?? '')
  const mine = data.texts.mine ?? ''
  return (
    <div className="stack">
      <p className={collected ? 'info-note' : 'muted'}>{note}</p>
      <PaneTitles titles={published === null ? [t('mergeColDraft')] : [t('mergeColPublished'), t('mergeColDraft')]} />
      <CodeMerge
        path={path}
        value={published ?? mine}
        right={published === null ? null : mine}
        readOnly
        label={t('mergeGridLabel')}
      />
    </div>
  )
}

/** Conflits restants dans le texte du milieu : marques du serveur, et marques de
 * `git merge-file` pour un fichier de structure inconnue. */
export function remainingConflicts(text: string, marker: string): number {
  const marks = marker ? text.split(marker).length - 1 : 0
  const blocks = text.split('\n').filter((line) => line.startsWith('<<<<<<< ')).length
  return marks + blocks
}

/** La fusion graphique : publié | résultat modifiable | brouillon. */
function GraphicalMerge({ data, path, busy, onSubmit, onCancel }: { data: MergeView; path: string; busy: boolean; onSubmit: (body: Body) => void; onCancel: () => void }) {
  const { t } = useTranslation()
  const initial = data.texts.proposed ?? data.texts.mine ?? ''
  const [text, setText] = useState(initial)
  const [showBase, setShowBase] = useState(false)
  const unresolved = remainingConflicts(text, data.marker)
  const error = unreadable(path, text)
  const hasBase = data.texts.base !== null
  return (
    <div className="stack">
      <div className="row merge-toolbar">
        <span className="muted small">{t('mergeSubtitle')}</span>
        <span className={unresolved > 0 ? 'merge-badge merge-badge-warn' : 'merge-badge merge-badge-ok'} role="status">
          {unresolved > 0 ? t('mergeRemaining', { count: unresolved }) : t('mergeResolvedLong')}
          {data.auto > 0 ? ` · ${t('mergeAutoCount', { count: data.auto })}` : ''}
        </span>
        <span className="merge-toolbar-gap" />
        <button
          type="button"
          className="btn btn-sm"
          aria-pressed={showBase}
          disabled={!hasBase}
          title={hasBase ? undefined : t('mergeNoBase')}
          onClick={() => setShowBase((v) => !v)}
        >
          {showBase ? t('mergeHideBase') : t('mergeShowBase')}
        </button>
      </div>
      {showBase && hasBase ? (
        <div className="stack stack-tight merge-base">
          <span className="field-label">{t('mergeSide_base')}</span>
          <CodeMerge path={path} value={data.texts.base ?? ''} readOnly label={t('mergeSide_base')} />
        </div>
      ) : null}
      <PaneTitles titles={[t('mergeColPublished'), t('mergeColResult'), t('mergeColDraft')]} />
      <CodeMerge
        path={path}
        left={data.texts.theirs ?? ''}
        value={initial}
        right={data.texts.mine ?? ''}
        onChange={setText}
        marker={data.marker}
        takeLeft={t('mergeTakeTheirsBlock')}
        takeRight={t('mergeTakeMineBlock')}
        label={t('mergeGridLabel')}
      />
      {error ? <p className="text-danger small">{error}</p> : null}
      <p className="muted small">{t('mergeHint', { marker: data.marker })}</p>
      <Footer unresolved={unresolved} valid={!error} busy={busy} onValidate={() => onSubmit({ content: text })} onCancel={onCancel} />
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
