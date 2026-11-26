import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import { ModelDiagram } from './ModelDiagram'
import { MODELS_TAB_PATH } from './ModelsTab'
import type { ArchimateDelegates } from './urbanArchimate'

// Page d'un modèle de la cartographie (on y arrive par « Ouvrir », ticket 5a05c7bb) :
// le diagramme occupe la page (ticket f5421cc7). Ossie lit le modèle et le passe
// hydraté au composant urban-archimate ; la boîte à outils (objets et relations
// existants de l'espace), la pose et les relations permises lui viennent d'Ossie,
// qui reste la seule source. Sans le droit « Cartographie : modifier » : lecture seule.

/** Le groupe des relations existantes de la boîte à outils (son libellé est traduit ici). */
const RELATIONS_GROUP = 'relations'

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
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('cartographie:write')
  const model = useQuery({
    queryKey: ['carto-model', spaceId, branchId, slug],
    queryFn: () => api.getCartoModel(spaceId, branchId, slug),
  })
  const hydrated = useQuery({
    queryKey: ['carto-model-hydrated', spaceId, branchId, slug],
    queryFn: () => api.getCartoModelHydrated(spaceId, branchId, slug),
    // Le composant garde le dessin en cours : on ne le recharge pas dans son dos.
    refetchOnWindowFocus: false,
  })
  const delegates = useMemo<ArchimateDelegates>(
    () => ({
      toolbox: async ({ pointDeVue }) => {
        const answer = await api.getCartoModelToolbox(spaceId, branchId, slug, pointDeVue ?? '')
        return answer.groupes.map((g) => (g.id === RELATIONS_GROUP ? { ...g, libelle: t('cartoToolboxRelations') } : g))
      },
      drop: (cle) => api.dropCartoModelObject(spaceId, branchId, slug, cle),
      allowedRelations: async (source, target) => {
        const answer = await api.listRelationTypes(spaceId, branchId, source, target)
        return answer.types.map((ty) => ({ type: ty.slug, archimate: ty.archimate, libelle: ty.title, schema: ty.type_schema }))
      },
    }),
    [spaceId, branchId, slug, t],
  )
  const data = model.data
  // Un modèle incohérent ne s'ouvre pas : ses problèmes sont nommés.
  const refused =
    hydrated.error instanceof ApiRequestError && Array.isArray(hydrated.error.body.error_args?.problems)
      ? (hydrated.error.body.error_args.problems as { code: string; detail: string; field: string }[])
      : []
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
            {!canWrite ? <span className="badge">{t('cartoReadOnly')}</span> : null}
          </div>
          {data?.description ? <p className="page-sub">{data.description}</p> : null}
        </div>
        <Link to={MODELS_TAB_PATH} className="btn btn-sm">
          {t('cartoBackToModels')}
        </Link>
      </div>
      <ErrorList messages={model.error ? errorMessages(t, model.error) : []} />
      <ErrorList messages={hydrated.error && refused.length === 0 ? errorMessages(t, hydrated.error) : []} />
      {refused.length > 0 ? (
        <ErrorList
          messages={[
            t('errors.carto_model_invalid'),
            ...refused.map((p) => t('cartoFileProblem', { code: p.code, field: p.detail || p.field })),
          ]}
        />
      ) : null}
      {hydrated.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {hydrated.data ? <ModelDiagram hydrated={hydrated.data} delegates={delegates} readOnly={!canWrite} /> : null}
    </main>
  )
}
