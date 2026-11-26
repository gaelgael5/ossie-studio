import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useWorkspace } from '../workspace'
import { MODELS_TAB_PATH } from './ModelsTab'

// Page d'un modèle de la cartographie (ticket 5a05c7bb : on y arrive par « Ouvrir ») ;
// le diagramme y est affiché par le composant urban-archimate (ticket f5421cc7).

export function CartoModelPage() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  const { slug = '' } = useParams()
  if (!ws.spaceId || !ws.branchId) {
    return (
      <main>
        {ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : <div className="empty-note">{t('noSpaceYet')}</div>}
      </main>
    )
  }
  return <ModelScreen spaceId={ws.spaceId} branchId={ws.branchId} slug={slug} />
}

function ModelScreen({ spaceId, branchId, slug }: { spaceId: string; branchId: string; slug: string }) {
  const { t } = useTranslation()
  const model = useQuery({
    queryKey: ['carto-model', spaceId, branchId, slug],
    queryFn: () => api.getCartoModel(spaceId, branchId, slug),
  })
  const data = model.data
  return (
    <main className="carto-model-page">
      <div className="breadcrumb">
        <Link to={MODELS_TAB_PATH}>{t('cartoTitle')}</Link> › {t('cartoTabModels')} › {data?.name ?? slug}
      </div>
      <div className="page-head">
        <div>
          <h1 className="page-title">{data?.name ?? slug}</h1>
          <div className="carto-type-head">
            {data?.id ? <span className="pill carto-key">{data.id}</span> : null}
            {data?.rules_name ? (
              <span className="carto-prop-chip">{t('cartoModelRules', { name: data.rules_name })}</span>
            ) : null}
            {data?.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
            {data?.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
          </div>
          {data?.description ? <p className="page-sub">{data.description}</p> : null}
        </div>
        <Link to={MODELS_TAB_PATH} className="btn btn-sm">
          {t('cartoBackToModels')}
        </Link>
      </div>
      <ErrorList messages={model.error ? errorMessages(t, model.error) : []} />
      {model.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {data && data.problems.length > 0 ? (
        <ErrorList messages={data.problems.map((p) => t('cartoFileProblem', { code: p.code, field: p.detail || p.field }))} />
      ) : null}
    </main>
  )
}
