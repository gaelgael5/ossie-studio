import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type { CartoBpmnObject, CartoObjectSummary } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import { UnsavedProvider, useSaveShortcut, useUnsaved } from '../referentiels/unsaved'
import { cartoObjectPath } from './ObjectsTab'
import { AttachProcessDialog, AttachPrompt, ObjectPickerDialog } from './ProcessDialogs'
import { ProcessEditor } from './ProcessEditor'
import { PROCESSES_TAB_PATH } from './ProcessesTab'
import { RelateDialog } from './RelateDialog'
import type { BpmnDelegates, BpmnElement } from './urbanBpmn'

// Page d'un processus BPMN de la cartographie (on y arrive par « Ouvrir », par l'import
// ou par la fiche d'un processus métier ; ticket 7e061c3e) : l'éditeur urban-bpmn y
// occupe la page (ticket 48335b93). Ossie lit le fichier et le passe tel quel, avec les
// objets référencés et leur nom à jour ; la boîte à outils (« Liés au processus »
// d'abord), la pose et ses avertissements, le rattachement d'un élément lui viennent
// d'Ossie, qui reste la seule source. Un processus non rattaché demande d'abord son
// processus métier (« Continuer sans » reste possible). « Créer la relation » ouvre la
// création de relation pré-remplie, sans quitter l'éditeur. Sans le droit
// « Cartographie : modifier » : lecture seule.

const EDITOR_ID = 'carto-process'
/** Les groupes de la boîte à outils, dont l'écran traduit les libellés. */
const GROUP_LABELS: Record<string, string> = {
  lies: 'cartoBpmnLinked',
  autres: 'cartoBpmnOthers',
  objets: 'cartoBpmnSpace',
}
const RELATION_ACTION = 'relation'
const KNOWN_RELATIONS = ['Assignment', 'Access', 'Serving', 'Aggregation']

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
  return (
    <UnsavedProvider>
      <ProcessScreen key={key} spaceId={ws.spaceId} branchId={ws.branchId} processKey={key} />
    </UnsavedProvider>
  )
}

type Relating = { source: CartoObjectSummary; target: CartoObjectSummary; archimate: string }
type Picking = { choices: CartoBpmnObject[]; current?: string; resolve: (chosen: CartoBpmnObject | null) => void }

function ProcessScreen({ spaceId, branchId, processKey }: { spaceId: string; branchId: string; processKey: string }) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('cartographie:write')
  const queryClient = useQueryClient()
  const element = useRef<BpmnElement | null>(null)
  // La collaboration que le dessin désigne, si une a été posée depuis l'ouverture.
  const collaboration = useRef('')
  const [dirty, setDirty] = useState(false)
  const [continued, setContinued] = useState(false)
  const [attaching, setAttaching] = useState(false)
  const [relating, setRelating] = useState<Relating | null>(null)
  const [picking, setPicking] = useState<Picking | null>(null)
  const process = useQuery({
    queryKey: ['carto-process', spaceId, branchId, processKey],
    queryFn: () => api.getCartoProcess(spaceId, branchId, processKey),
  })
  const content = useQuery({
    queryKey: ['carto-process', spaceId, branchId, processKey, 'contenu'],
    queryFn: () => api.getCartoProcessContent(spaceId, branchId, processKey),
    // L'éditeur garde le dessin en cours : on ne le recharge pas dans son dos.
    refetchOnWindowFocus: false,
  })
  const objects = useQuery({
    queryKey: ['carto-objects', spaceId, branchId],
    queryFn: () => api.listCartoObjects(spaceId, branchId),
    enabled: canWrite,
  })
  const catalog = useQuery({
    queryKey: ['carto-catalog'],
    queryFn: () => api.getCartoCatalog(spaceId, branchId),
    staleTime: Infinity,
    enabled: canWrite,
  })
  const summaries = objects.data?.objects
  const choices = (summaries ?? [])
    .filter((o) => o.archimate === 'BusinessProcess')
    .map((o) => ({ key: o.key, ref: o.ref, name: o.name }))

  const delegates = useMemo<BpmnDelegates>(
    () => ({
      toolbox: async () => {
        const answer = await api.getCartoProcessToolbox(spaceId, branchId, processKey)
        return answer.groupes.map((g) => (GROUP_LABELS[g.id] ? { ...g, libelle: t(GROUP_LABELS[g.id]) } : g))
      },
      drop: async (cle) => {
        const dropped = await api.dropCartoProcessObject(spaceId, branchId, processKey, cle, collaboration.current)
        if (dropped.genre === 'collaboration') collaboration.current = cle
        const missing = dropped.avertissement
        if (!missing) return { ...dropped, avertissement: undefined }
        const relation = KNOWN_RELATIONS.includes(missing.relation) ? missing.relation : 'other'
        return {
          ...dropped,
          avertissement: {
            message: t(`cartoBpmnMissing_${relation}`, { source: missing.source_name, target: missing.target_name }),
            action: {
              id: [RELATION_ACTION, missing.relation, missing.source, missing.target].join('|'),
              libelle: t('cartoBpmnCreateRelation'),
            },
          },
        }
      },
      action: (_cle, action) => {
        const [kind, archimate, source, target] = action.split('|')
        // La relation qui manque désigne ses extrémités par `type/CLÉ` (ticket 421832ee).
        const found = (ref: string) => summaries?.find((o) => o.ref === ref)
        const from = found(source)
        const to = found(target)
        if (kind === RELATION_ACTION && from && to) setRelating({ source: from, target: to, archimate })
      },
      attach: async (context) => {
        const answer = await api.listCartoProcessAttachable(spaceId, branchId, processKey, context.type)
        return new Promise<CartoBpmnObject | null>((resolve) =>
          setPicking({ choices: answer.objets, current: context.cle, resolve }),
        )
      },
    }),
    [spaceId, branchId, processKey, summaries, t],
  )
  const save = useMutation({
    mutationFn: async () => {
      if (!element.current) throw new Error('éditeur absent')
      const { xml } = await element.current.save()
      return api.saveCartoProcess(spaceId, branchId, processKey, xml)
    },
    onSuccess: async () => {
      setDirty(false)
      // Le contenu n'est pas relu : l'éditeur garde le dessin enregistré.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['carto-process', spaceId, branchId, processKey], exact: true }),
        ...['carto-processes', 'carto-object', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      ])
    },
  })
  const data = process.data
  useUnsaved(EDITOR_ID, { label: data?.name ?? processKey, dirty: canWrite && dirty, saveable: true }, () =>
    save.mutateAsync(),
  )
  useSaveShortcut(() => EDITOR_ID)
  const detail = data?.detail_of
  const asking = canWrite && data !== undefined && !detail && !continued
  const saveProblems = problemsOf(save.error)
  return (
    <main className="carto-model-page">
      <div className="breadcrumb">
        {t('cartoTitle')} › <Link to={PROCESSES_TAB_PATH}>{t('cartoTabProcesses')}</Link> › {data?.name ?? processKey}
      </div>
      <div className="page-head">
        <div>
          <h1 className="page-title">{data?.name ?? processKey}</h1>
          <div className="carto-type-head">
            <span className="pill carto-key">{processKey}</span>
            {data?.state === 'created' ? <span className="badge carto-badge-new">{t('cartoNew')}</span> : null}
            {data?.state === 'modified' ? <span className="badge badge-warn">{t('cartoUnpublished')}</span> : null}
            {!canWrite ? <span className="badge">{t('cartoReadOnly')}</span> : null}
            {data ? (
              detail ? (
                <span className="carto-process-detail">
                  {t('cartoProcessDetailOf')} <Link to={cartoObjectPath(detail.ref)}>{detail.name ?? detail.key}</Link>
                </span>
              ) : (
                <span className="carto-model-warn carto-process-unattached">{t('cartoProcessUnattached')}</span>
              )
            ) : null}
          </div>
        </div>
        <div className="page-actions">
          {dirty ? <span className="carto-changed">{t('cartoModelChanged')}</span> : null}
          <Link to={PROCESSES_TAB_PATH} className="btn">
            {t('cartoBackToProcesses')}
          </Link>
          {canWrite && data && !asking ? (
            <button
              type="button"
              className="btn"
              disabled={dirty}
              title={dirty ? t('cartoProcessSaveFirst') : undefined}
              onClick={() => setAttaching(true)}
            >
              {t('cartoProcessAttach')}
            </button>
          ) : null}
          {canWrite && !asking ? (
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
      <ErrorList messages={process.error ? errorMessages(t, process.error) : []} />
      <ErrorList messages={content.error ? errorMessages(t, content.error) : []} />
      {data && data.problems.length > 0 ? (
        <ErrorList messages={data.problems.map((p) => t('cartoFileProblem', { code: p.code, field: p.detail || p.field }))} />
      ) : null}
      <ErrorList
        messages={
          save.error
            ? [
                ...errorMessages(t, save.error),
                ...saveProblems.map((p) => t('cartoFileProblem', { code: p.code, field: p.detail || p.field })),
              ]
            : []
        }
      />
      {process.isLoading || content.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {asking && data ? (
        <AttachPrompt
          spaceId={spaceId}
          branchId={branchId}
          process={data}
          choices={choices}
          onContinue={() => setContinued(true)}
        />
      ) : null}
      {!asking && data && content.data ? (
        <ProcessEditor
          xml={content.data.content}
          references={content.data.references}
          delegates={delegates}
          readOnly={!canWrite}
          onChanged={() => setDirty(true)}
          onReady={(ready) => {
            element.current = ready
            setDirty(false)
          }}
        />
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
      {relating && catalog.data && summaries ? (
        <RelateDialog
          spaceId={spaceId}
          branchId={branchId}
          catalog={catalog.data}
          source={relating.source}
          objects={summaries}
          initialTarget={relating.target}
          initialArchimate={relating.archimate}
          onCreated={() => undefined}
          onClose={() => setRelating(null)}
        />
      ) : null}
      {picking ? (
        <ObjectPickerDialog
          choices={picking.choices}
          current={picking.current}
          onChoose={(chosen) => {
            picking.resolve(chosen)
            setPicking(null)
          }}
          onCancel={() => {
            picking.resolve(null)
            setPicking(null)
          }}
        />
      ) : null}
    </main>
  )
}

type Problem = { code: string; detail: string; field: string }

/** Les problèmes nommés d'un refus de l'API (référence inconnue…). */
function problemsOf(error: unknown): Problem[] {
  return error instanceof ApiRequestError && Array.isArray(error.body.error_args?.problems)
    ? (error.body.error_args.problems as Problem[])
    : []
}
