import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CartoViewNote, useCartoWrite } from './viewGate'
import { Link, useParams } from 'react-router-dom'
import { api } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useWorkspace } from '../workspace'
import { UnsavedProvider, useSaveShortcut, useUnsaved } from '../referentiels/unsaved'
import { ModelDiagram } from './ModelDiagram'
import { MODELS_TAB_PATH } from './ModelsTab'
import type { ArchimateDelegates, ArchimateElement } from './urbanArchimate'

// Page d'un modèle de la cartographie (on y arrive par « Ouvrir », ticket 5a05c7bb) :
// le diagramme occupe la page (ticket f5421cc7). Ossie lit le modèle et le passe
// hydraté au composant urban-archimate ; la boîte à outils (objets et relations
// existants de l'espace), la pose et les relations permises lui viennent d'Ossie,
// qui reste la seule source. Sans le droit « Cartographie : modifier » : lecture seule.
// « Enregistrer » (ticket c3dd045c) écrit ce que rend le composant (modèle, graphique,
// nouvelles relations) en un seul enregistrement du brouillon, puis recharge le modèle.

/** Le groupe des relations existantes de la boîte à outils (son libellé est traduit ici). */
const RELATIONS_GROUP = 'relations'
const EDITOR_ID = 'carto-model'

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
  return (
    <UnsavedProvider>
      <ModelScreen key={slug} spaceId={ws.spaceId} branchId={ws.branchId} slug={slug} />
    </UnsavedProvider>
  )
}

function ModelScreen({ spaceId, branchId, slug }: { spaceId: string; branchId: string; slug: string }) {
  const { t } = useTranslation()
  const { canWrite } = useCartoWrite(branchId)
  const queryClient = useQueryClient()
  const element = useRef<ArchimateElement | null>(null)
  const [dirty, setDirty] = useState(false)
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
  const save = useMutation({
    mutationFn: () => {
      if (!element.current) throw new Error('diagramme absent')
      const { modele, graphique, nouvellesRelations } = element.current.save()
      return api.saveCartoModel(spaceId, branchId, slug, { modele, graphique, nouvellesRelations })
    },
    onSuccess: async () => {
      setDirty(false)
      // Les nouvelles relations sont désormais des objets : le modèle est réhydraté.
      await Promise.all(
        ['carto-model', 'carto-model-hydrated', 'carto-models', 'carto-objects', 'carto-object', 'draft', 'draftChanges'].map(
          (k) => queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
    },
  })
  useUnsaved(EDITOR_ID, { label: model.data?.name ?? slug, dirty: canWrite && dirty, saveable: true }, () =>
    save.mutateAsync(),
  )
  useSaveShortcut(() => EDITOR_ID)
  const data = model.data
  // Un modèle incohérent ne s'ouvre pas : ses problèmes sont nommés.
  const refused = problemsOf(hydrated.error)
  const saveProblems = problemsOf(save.error)
  return (
    <main className="carto-model-page">
      <CartoViewNote branchId={branchId} />
      <div className="breadcrumb">
        {t('cartoTitle')} › <Link to={MODELS_TAB_PATH}>{t('cartoTabModels')}</Link> › {data?.name ?? slug}
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
        <div className="page-actions">
          {dirty ? <span className="carto-changed">{t('cartoModelChanged')}</span> : null}
          <Link to={MODELS_TAB_PATH} className="btn">
            {t('cartoBackToModels')}
          </Link>
          {canWrite ? (
            <BusyButton
              type="button"
              className="btn btn-primary"
              busy={save.isPending}
              busyText={t('cartoModelSaveBusy')}
              disabled={!dirty}
              onClick={() => save.mutate()}
            >
              {t('cartoModelSave')}
            </BusyButton>
          ) : null}
        </div>
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
      <ErrorList
        messages={
          save.error
            ? [
                ...errorMessages(t, save.error),
                ...saveProblems.map((p) => t('cartoFileProblem', { code: p.code, field: p.field || p.detail })),
              ]
            : []
        }
      />
      {hydrated.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {hydrated.data ? (
        <ModelDiagram
          hydrated={hydrated.data}
          delegates={delegates}
          readOnly={!canWrite}
          onChanged={() => setDirty(true)}
          onReady={(ready) => {
            element.current = ready
            setDirty(false)
          }}
        />
      ) : null}
    </main>
  )
}

type Problem = { code: string; detail: string; field: string }

/** Les problèmes nommés d'un refus de l'API (modèle incohérent, relation refusée). */
function problemsOf(error: unknown): Problem[] {
  return error instanceof ApiRequestError && Array.isArray(error.body.error_args?.problems)
    ? (error.body.error_args.problems as Problem[])
    : []
}
