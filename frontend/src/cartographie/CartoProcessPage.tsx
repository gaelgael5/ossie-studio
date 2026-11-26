import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import { cartoObjectPath } from './ObjectsTab'
import { AttachProcessDialog } from './ProcessDialogs'
import { ProcessPreview } from './ProcessPreview'
import { PROCESSES_TAB_PATH } from './ProcessesTab'

// Page d'un processus BPMN de la cartographie (on y arrive par « Ouvrir », par l'import
// ou par la fiche d'un processus métier ; ticket 7e061c3e) : son dessin, ses nombres, le
// processus métier dont il est le détail (ou « Rattacher… »). L'éditeur urban-bpmn y
// viendra avec le ticket « Ouvrir et enregistrer un processus » (48335b93).

export function CartoProcessPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const { key = '' } = useParams()
  if (!ws.spaceId || !ws.branchId) {
    return (
      <main>
        {ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : <div className="empty-note">{t('noSpaceYet')}</div>}
      </main>
    )
  }
  return <ProcessScreen spaceId={ws.spaceId} branchId={ws.branchId} processKey={key} />
}

function ProcessScreen({ spaceId, branchId, processKey }: { spaceId: string; branchId: string; processKey: string }) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('cartographie:write')
  const [attaching, setAttaching] = useState(false)
  const process = useQuery({
    queryKey: ['carto-process', spaceId, branchId, processKey],
    queryFn: () => api.getCartoProcess(spaceId, branchId, processKey),
  })
  const objects = useQuery({
    queryKey: ['carto-objects', spaceId, branchId],
    queryFn: () => api.listCartoObjects(spaceId, branchId),
    enabled: canWrite,
  })
  const choices = (objects.data?.objects ?? [])
    .filter((o) => o.archimate === 'BusinessProcess')
    .map((o) => ({ key: o.key, name: o.name }))
  const data = process.data
  const detail = data?.detail_of
  return (
    <main className="carto-model-page">
      <div className="breadcrumb">
        <Link to={PROCESSES_TAB_PATH}>{t('cartoTitle')}</Link> › {t('cartoTabProcesses')} › {data?.name ?? processKey}
      </div>
      <div className="page-head">
        <div>
          <h1 className="page-title">{data?.name ?? processKey}</h1>
          <div className="carto-type-head">
            <span className="pill carto-key">{processKey}</span>
            {data?.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
            {data?.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
            {!canWrite ? <span className="badge">{t('cartoReadOnly')}</span> : null}
          </div>
          {data ? (
            detail ? (
              <p className="page-sub">
                {t('cartoProcessDetailOf')} <Link to={cartoObjectPath(detail.key)}>{detail.name ?? detail.key}</Link>
              </p>
            ) : (
              <p className="carto-model-warn carto-process-unattached">{t('cartoProcessUnattached')}</p>
            )
          ) : null}
        </div>
        <div className="page-actions">
          <Link to={PROCESSES_TAB_PATH} className="btn">
            {t('cartoBackToProcesses')}
          </Link>
          {canWrite && data ? (
            <button type="button" className="btn" onClick={() => setAttaching(true)}>
              {t('cartoProcessAttach')}
            </button>
          ) : null}
        </div>
      </div>
      <ErrorList messages={process.error ? errorMessages(t, process.error) : []} />
      {data && data.problems.length > 0 ? (
        <ErrorList messages={data.problems.map((p) => t('cartoFileProblem', { code: p.code, field: p.detail || p.field }))} />
      ) : null}
      {process.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {data ? (
        <>
          <div className="carto-chips">
            <span className="carto-prop-chip">{t('cartoParticipantsCount', { count: data.participants })}</span>
            <span className="carto-prop-chip">{t('cartoLanesCount', { count: data.lanes })}</span>
            <span className="carto-prop-chip">{t('cartoAttachedCount', { count: data.attached })}</span>
            <span className="carto-prop-chip mono">{data.path}</span>
          </div>
          <div className="carto-process-canvas">
            <ProcessPreview preview={data.preview} width={1100} height={480} labels />
          </div>
        </>
      ) : null}
      {attaching && data ? (
        <AttachProcessDialog
          spaceId={spaceId}
          branchId={branchId}
          process={data}
          choices={choices}
          onClose={() => setAttaching(false)}
        />
      ) : null}
    </main>
  )
}
