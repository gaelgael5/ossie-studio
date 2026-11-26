import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { CartoProcessSummary } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { asciiLower } from '../slug'
import { shortDate } from './dates'
import { cartoObjectPath } from './ObjectsTab'
import { ProcessPreview } from './ProcessPreview'

// Onglet Processus de la Cartographie (epic « Cartographie — processus BPMN », ticket
// 7e061c3e, maquette validée par Gael le 26 novembre) : les processus BPMN en vignettes,
// avec l'aperçu de leur dessin, une barre de filtre (recherche, Tous / Rattachés / Non
// rattachés, participant), « + Nouveau processus » et « Importer un fichier BPMN ». Un
// processus non rattaché à un processus métier est signalé, avec « Rattacher… ».

export const PROCESSES_TAB_PATH = '/cartographie/processus'

export function cartoProcessPath(key: string): string {
  return `${PROCESSES_TAB_PATH}/${encodeURIComponent(key)}`
}

type Attachment = 'all' | 'attached' | 'unattached'

export function ProcessesTab({
  spaceId,
  branchId,
  processes,
  editable,
  onNew,
  onImport,
  onAttach,
}: {
  spaceId: string
  branchId: string
  processes: CartoProcessSummary[]
  editable: boolean
  onNew: () => void
  onImport: () => void
  onAttach: (process: CartoProcessSummary) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [attachment, setAttachment] = useState<Attachment>('all')
  const [pool, setPool] = useState('')
  const remove = useMutation({
    mutationFn: (key: string) => api.deleteCartoProcess(spaceId, branchId, key),
    onSuccess: async () => {
      await Promise.all(
        ['carto-processes', 'carto-object', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
    },
  })
  const pools = [...new Set(processes.flatMap((p) => p.pools))].sort((a, b) => a.localeCompare(b))
  const needle = asciiLower(search.trim())
  const shown = processes
    .filter((p) => !needle || asciiLower(`${p.name} ${p.key} ${p.detail_of?.name ?? ''}`).includes(needle))
    .filter((p) => attachment === 'all' || (attachment === 'attached') === (p.detail_of !== null))
    .filter((p) => !pool || p.pools.includes(pool))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) || a.key.localeCompare(b.key))
  return (
    <>
      <div className="panel carto-filters carto-object-filters">
        <div className="carto-filter-row">
          <input
            type="search"
            className="input carto-object-search"
            aria-label={t('cartoProcessSearch')}
            placeholder={t('cartoProcessSearch')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="carto-filter-group" role="group" aria-label={t('cartoProcessAttachment')}>
            {(['all', 'attached', 'unattached'] as const).map((value) => (
              <button
                key={value}
                type="button"
                className={`curation-chip${attachment === value ? ' active' : ''}`}
                aria-pressed={attachment === value}
                onClick={() => setAttachment(value)}
              >
                {t(`cartoProcessAttachment_${value}`)}
              </button>
            ))}
          </div>
          <label className="carto-filter-group">
            <span className="carto-filter-label">{t('cartoProcessParticipant')}</span>
            <select className="select carto-model-contains" value={pool} onChange={(e) => setPool(e.target.value)}>
              <option value="">{t('cartoModelContainsAll')}</option>
              {pools.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <span className="carto-object-count">{t('cartoProcessesCount', { count: processes.length })}</span>
        </div>
      </div>
      <ErrorList messages={remove.error ? errorMessages(t, remove.error) : []} />
      {processes.length > 0 && shown.length === 0 ? <p className="empty-note">{t('cartoProcessesNone')}</p> : null}
      {processes.length === 0 && !editable ? <p className="empty-note">{t('cartoProcessesEmpty')}</p> : null}
      <div className="carto-grid carto-model-grid">
        {shown.map((p) => (
          <ProcessCard
            key={p.key}
            process={p}
            deleting={remove.isPending && remove.variables === p.key}
            onDelete={editable ? () => remove.mutate(p.key) : null}
            onAttach={editable ? () => onAttach(p) : null}
          />
        ))}
        {editable ? (
          <div className="carto-card carto-card-add carto-model-add">
            <button type="button" className="carto-add-link" onClick={onNew}>
              {t('cartoNewProcess')}
            </button>
            <button type="button" className="carto-add-sub" onClick={onImport}>
              {t('cartoNewProcessOrImport')}
            </button>
          </div>
        ) : null}
      </div>
    </>
  )
}

function ProcessCard({
  process,
  deleting,
  onDelete,
  onAttach,
}: {
  process: CartoProcessSummary
  deleting: boolean
  onDelete: (() => void) | null
  onAttach: (() => void) | null
}) {
  const { t, i18n } = useTranslation()
  const [menu, setMenu] = useState<'closed' | 'open' | 'confirm'>('closed')
  const dates = process.dates
  const when = dates
    ? dates.created_at === dates.updated_at
      ? t('cartoCreatedOn', { date: shortDate(dates.created_at, i18n.resolvedLanguage) })
      : t('cartoUpdatedOn', { date: shortDate(dates.updated_at, i18n.resolvedLanguage) })
    : t('cartoUnpublishedDate')
  const detail = process.detail_of
  return (
    <article className="carto-card carto-card-link carto-object-card carto-model-card">
      <Link to={cartoProcessPath(process.key)} className="carto-card-cover" aria-label={process.name} />
      <div className="carto-model-thumb">
        <ProcessPreview preview={process.preview} />
      </div>
      <div className="carto-model-body">
        <div className="carto-object-head">
          <h2 className="carto-card-title">{process.name}</h2>
          {process.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
          {process.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
        </div>
        <span className="pill carto-key">{process.key}</span>
        {detail ? (
          <p className="carto-process-detail">
            {t('cartoProcessDetailOf')}{' '}
            <Link to={cartoObjectPath(detail.key)} className="carto-over-cover">
              {detail.name ?? detail.key}
            </Link>
          </p>
        ) : (
          <p className="carto-model-warn">{t('cartoProcessUnattached')}</p>
        )}
        {process.errors > 0 ? <p className="carto-model-warn">{t('cartoModelProblems', { count: process.errors })}</p> : null}
        <div className="carto-chips">
          <span className="carto-prop-chip">{t('cartoParticipantsCount', { count: process.participants })}</span>
          <span className="carto-prop-chip">{t('cartoLanesCount', { count: process.lanes })}</span>
          <span className="carto-prop-chip">{t('cartoAttachedCount', { count: process.attached })}</span>
        </div>
      </div>
      <div className="carto-card-foot">
        <span>{when}</span>
        {!detail && onAttach ? (
          <button type="button" className="btn btn-sm carto-over-cover carto-process-attach" onClick={onAttach}>
            {t('cartoProcessAttach')}
          </button>
        ) : (
          <span className="mono muted small carto-model-author">{dates?.updated_by ?? ''}</span>
        )}
        <span className="carto-model-actions">
          {onDelete ? (
            <button
              type="button"
              className="btn btn-sm"
              aria-label={t('cartoProcessMenu')}
              aria-expanded={menu !== 'closed'}
              onClick={() => setMenu(menu === 'closed' ? 'open' : 'closed')}
            >
              {'…'}
            </button>
          ) : null}
          <Link to={cartoProcessPath(process.key)} className="btn btn-sm btn-primary">
            {t('cartoModelOpen')}
          </Link>
        </span>
        {onDelete && menu !== 'closed' ? (
          <div className="carto-model-menu" role="menu">
            {menu === 'open' ? (
              <button type="button" role="menuitem" className="btn btn-sm" onClick={() => setMenu('confirm')}>
                {t('cartoDeleteProcess')}
              </button>
            ) : (
              <>
                <BusyButton
                  type="button"
                  className="btn btn-sm btn-danger"
                  busy={deleting}
                  busyText={t('cartoDeleteBusy')}
                  onClick={onDelete}
                >
                  {t('cartoDeleteModelYes')}
                </BusyButton>
                <button type="button" className="btn btn-sm" onClick={() => setMenu('closed')}>
                  {t('cancelEdit')}
                </button>
              </>
            )}
          </div>
        ) : null}
      </div>
    </article>
  )
}
