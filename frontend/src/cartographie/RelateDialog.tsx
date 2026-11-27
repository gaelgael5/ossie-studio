import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { api } from '../apiClient'
import type { CartoCatalog, CartoObjectSummary, CartoRelationTypeChoice } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import type { FieldProblems } from '../referentiels/ValuePanel'
import { asciiLower } from '../slug'
import { ArchimateIcon } from './ArchimateIcon'
import { FoldedProperties, missingRequired } from './FoldedProperties'
import type { Props } from './FoldedProperties'
import { cartoObjectPath } from './ObjectsTab'

// Fenêtre « Nouvelle relation » (ticket b58a0a13, maquette « popup Nouvelle
// relation ») : depuis un objet ou une relation (la source), choisir la cible (un
// objet ou une relation), puis le type parmi les seuls types que les règles
// d'ArchiMate permettent entre les deux, puis la clé, le nom, les attributs du
// type (sens d'un accès…) et les propriétés ajoutées. La relation est écrite dans
// son propre fichier ; sa documentation se rédige ensuite dans sa fiche.
// Depuis l'éditeur BPMN (« Créer la relation », ticket 48335b93) : la cible et le type
// arrivent pré-remplis, et la fenêtre se ferme sans quitter l'éditeur (`onCreated`).

const KEY_FORMAT = /^[A-Z0-9]+(?:-[A-Z0-9]+)*$/
const RESULTS = 8

export interface RelateSource {
  key: string
  name: string
  type_title: string
  archimate: string
}

/** La clé sans son préfixe (« PRC-PRISE-COMMANDE » → « PRISE-COMMANDE »). */
function stem(key: string): string {
  const parts = key.split('-')
  return parts.length > 1 ? parts.slice(1).join('-') : key
}

/** Une clé proposée à partir des deux clés, libre dans l'espace. */
export function proposedKey(source: string, target: string, taken: Set<string>): string {
  const base = `REL-${stem(source)}-${stem(target)}`
  if (!taken.has(base)) return base
  let n = 2
  while (taken.has(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}

function capitalize(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

export function RelateDialog({
  spaceId,
  branchId,
  catalog,
  source,
  objects,
  onClose,
  initialTarget = null,
  initialArchimate = '',
  onCreated,
}: {
  spaceId: string
  branchId: string
  catalog: CartoCatalog
  source: RelateSource
  objects: CartoObjectSummary[]
  onClose: () => void
  /** La cible déjà choisie. */
  initialTarget?: CartoObjectSummary | null
  /** Le type ArchiMate à choisir d'office, s'il est permis et que l'espace en a un. */
  initialArchimate?: string
  /** Après la création, à la place d'ouvrir la fiche de la relation. */
  onCreated?: (key: string) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [target, setTarget] = useState<CartoObjectSummary | null>(initialTarget)
  const [choice, setChoice] = useState<CartoRelationTypeChoice | null>(null)
  const [key, setKey] = useState(() =>
    initialTarget ? proposedKey(source.key, initialTarget.key, new Set(objects.map((o) => o.key))) : '',
  )
  const [keyTouched, setKeyTouched] = useState(false)
  const [name, setName] = useState('')
  const [nameTouched, setNameTouched] = useState(false)
  const [properties, setProperties] = useState<Props>({})
  const [opened, setOpened] = useState<string[]>([])
  const byName = useMemo(() => new Map(catalog.types.map((ty) => [ty.name, ty])), [catalog])
  const taken = useMemo(() => new Set(objects.map((o) => o.key)), [objects])
  const needle = asciiLower(search.trim())
  const results = objects
    .filter((o) => o.key !== source.key && (!needle || asciiLower(`${o.name} ${o.key}`).includes(needle)))
    .slice(0, RESULTS)
  const allowed = useQuery({
    queryKey: ['carto-relation-types', spaceId, branchId, source.key, target?.key],
    queryFn: () => api.listRelationTypes(spaceId, branchId, source.key, target!.key),
    enabled: target !== null,
  })
  const withoutType = (allowed.data?.archimate ?? []).filter((a) => !(allowed.data?.types ?? []).some((ty) => ty.archimate === a))

  // Nom et clé proposés tant que la personne ne les a pas saisis.
  const propose = (nextTarget: CartoObjectSummary | null, nextChoice: CartoRelationTypeChoice | null) => {
    if (nextTarget && !keyTouched) setKey(proposedKey(source.key, nextTarget.key, taken))
    if (nextTarget && nextChoice && !nameTouched) {
      const verb = byName.get(nextChoice.archimate)?.verbs?.out ?? nextChoice.title
      setName(capitalize(`${verb} ${nextTarget.name}`))
    }
  }
  const chooseTarget = (next: CartoObjectSummary) => {
    setTarget(next)
    setChoice(null)
    setProperties({})
    setOpened([])
    propose(next, null)
  }
  const chooseType = (next: CartoRelationTypeChoice) => {
    setChoice(next)
    setProperties({})
    setOpened([])
    propose(target, next)
  }

  // Le type demandé, choisi d'office une fois les types permis connus.
  const latestChoose = useRef(chooseType)
  latestChoose.current = chooseType
  const preset = useRef(false)
  useEffect(() => {
    if (preset.current || !initialArchimate) return
    const found = allowed.data?.types.find((ty) => ty.archimate === initialArchimate)
    if (found) {
      preset.current = true
      latestChoose.current(found)
    }
  }, [allowed.data, initialArchimate])

  const keyError = !key ? null : !KEY_FORMAT.test(key) ? t('cartoKeyFormat') : taken.has(key) ? t('cartoKeyTaken') : null
  const schema = choice?.object_schema ?? null
  const missing = missingRequired(schema, properties)
  const ready = Boolean(target && choice && key && !keyError && name.trim() && missing.length === 0)
  const create = useMutation({
    mutationFn: () =>
      api.createCartoObject(spaceId, branchId, {
        key,
        type: choice!.slug,
        name: name.trim(),
        documentation: '',
        properties,
        source: source.key,
        target: target!.key,
      }),
    onSuccess: async (saved) => {
      await Promise.all(
        ['carto-objects', 'carto-object', 'carto-types', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
      onClose()
      if (onCreated) onCreated(saved.key)
      else navigate(cartoObjectPath(saved.key), { state: { from: { key: source.key, name: source.name } } })
    },
  })
  const problems: FieldProblems = new Map(missing.map((p) => [p, ['value_required']]))
  const serverProblems =
    create.error instanceof ApiRequestError && Array.isArray(create.error.body.error_args?.problems)
      ? (create.error.body.error_args.problems as { code: string; field: string }[])
      : []
  const arrowLabel = choice
    ? [choice.title, attributeLabel(choice, properties, byName)].filter(Boolean).join(' · ')
    : t('cartoRelateChooseType')
  return (
    <div className="dialog-backdrop">
      <form
        role="dialog"
        aria-label={t('cartoRelateTitle')}
        className="panel dialog-panel dialog-wide stack"
        onSubmit={(e) => {
          e.preventDefault()
          if (ready) create.mutate()
        }}
      >
        <div className="row dialog-head">
          <h2>{t('cartoRelateTitle')}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={create.error ? errorMessages(t, create.error) : []} />
        {serverProblems.length > 0 ? (
          <ul className="carto-problems">
            {serverProblems.map((p, i) => (
              <li key={i} className="mono small">
                {p.field ? `${p.field} · ` : ''}
                {t(`cartoProblem_${p.code}`, { defaultValue: p.code })}
              </li>
            ))}
          </ul>
        ) : null}
        <div className="carto-band carto-relate-band">
          <EndCard label={t('cartoEnd_source')} name={source.name} keyValue={source.key} typeTitle={source.type_title} icon={byName.get(source.archimate)?.icon} />
          <div className="carto-arrow">
            <span>{arrowLabel}</span>
            <span className="carto-arrow-line" aria-hidden="true" />
          </div>
          {target ? (
            <EndCard
              label={t('cartoEnd_target')}
              name={target.name}
              keyValue={target.key}
              typeTitle={target.is_relation ? t('cartoRelationOf', { type: target.type_title }) : target.type_title}
              icon={byName.get(target.archimate)?.icon}
            />
          ) : (
            <div className="carto-end carto-end-empty muted">{t('cartoRelateChooseTarget')}</div>
          )}
        </div>
        <div className="carto-add">
          <div className="stack">
            <h3 className="carto-detail-title">{t('cartoRelateStepTarget')}</h3>
            <input
              type="search"
              className="input"
              aria-label={t('cartoObjectSearch')}
              placeholder={t('cartoObjectSearch')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="carto-type-list" role="listbox" aria-label={t('cartoRelateStepTarget')}>
              {results.length === 0 ? <p className="muted">{t('cartoObjectsNone')}</p> : null}
              {results.map((o) => (
                <button
                  key={o.key}
                  type="button"
                  role="option"
                  aria-selected={target?.key === o.key}
                  className={`carto-type-item carto-target-item${target?.key === o.key ? ' active' : ''}`}
                  onClick={() => chooseTarget(o)}
                >
                  {byName.get(o.archimate) ? <ArchimateIcon svg={byName.get(o.archimate)!.icon} /> : null}
                  <span>{o.name}</span>
                  <span className="mono muted small">{o.key}</span>
                  <span className="mono muted small carto-target-type">
                    {o.is_relation ? t('cartoRelationOf', { type: o.type_title }) : o.type_title}
                  </span>
                </button>
              ))}
            </div>
            <h3 className="carto-detail-title">{t('cartoRelateStepType')}</h3>
            {!target ? <p className="muted">{t('cartoRelateChooseTarget')}</p> : null}
            {target && allowed.isLoading ? <p className="muted">{t('loading')}</p> : null}
            <ErrorList messages={allowed.error ? errorMessages(t, allowed.error) : []} />
            {target && allowed.data && allowed.data.archimate.length === 0 ? (
              <p className="empty-note">{t('cartoRelateNone')}</p>
            ) : null}
            <div className="stack" role="radiogroup" aria-label={t('cartoRelateStepType')}>
              {(allowed.data?.types ?? []).map((ty) => {
                const info = byName.get(ty.archimate)
                return (
                  <label key={ty.slug} className={`carto-choice${choice?.slug === ty.slug ? ' active' : ''}`}>
                    <input type="radio" name="carto-relation-type" checked={choice?.slug === ty.slug} onChange={() => chooseType(ty)} />
                    <span>
                      <strong>{ty.title}</strong>
                      <span className="muted small carto-choice-def">
                        {byName.get(source.archimate)?.label ?? source.archimate} → {byName.get(target?.archimate ?? '')?.label ?? target?.archimate} :{' '}
                        {info?.definition}
                      </span>
                    </span>
                  </label>
                )
              })}
            </div>
            {withoutType.length > 0 ? (
              <p className="muted small">
                {t('cartoRelateWithoutType', { types: withoutType.map((a) => byName.get(a)?.label ?? a).join(', ') })}
              </p>
            ) : null}
          </div>
          <div className="stack">
            <h3 className="carto-detail-title">{t('cartoRelateStepRelation')}</h3>
            <div className="field">
              <label className="field-label" htmlFor="carto-relate-key">
                {t('cartoKey')} <span className="text-danger">*</span>
              </label>
              <input
                id="carto-relate-key"
                className="input mono"
                value={key}
                onChange={(e) => {
                  setKey(e.target.value.toUpperCase())
                  setKeyTouched(true)
                }}
              />
              {keyError ? <span className="field-error">{keyError}</span> : null}
              <span className="field-hint">{t('cartoRelateKeyHint')}</span>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="carto-relate-name">
                {t('cartoName')} <span className="text-danger">*</span>
              </label>
              <input
                id="carto-relate-name"
                className="input"
                value={name}
                onChange={(e) => {
                  setName(e.target.value)
                  setNameTouched(true)
                }}
              />
            </div>
            {schema ? (
              <FoldedProperties
                schema={schema}
                values={properties}
                opened={opened}
                readOnly={false}
                problems={problems}
                onChange={setProperties}
                onOpen={(p) => setOpened((list) => [...list, p])}
              />
            ) : null}
            {choice && key ? (
              <p className="info-note small">
                {t('cartoRelateFile')} <span className="mono">{`cartographie/objets/${choice.slug}/${key.toLowerCase()}.yaml`}</span>
              </p>
            ) : null}
          </div>
        </div>
        <div className="row modal-foot carto-relate-foot">
          <span className="muted small">{t('cartoRelateDocLater')}</span>
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton type="submit" className="btn btn-primary" busy={create.isPending} busyText={t('refSaveBusy')} disabled={!ready}>
            {t('cartoRelateCreate')}
          </BusyButton>
        </div>
      </form>
    </div>
  )
}

/** Le libellé de l'attribut renseigné d'une relation (« écriture » pour un accès). */
function attributeLabel(
  choice: CartoRelationTypeChoice,
  properties: Props,
  byName: Map<string, CartoCatalog['types'][number]>,
): string {
  for (const a of byName.get(choice.archimate)?.attributes ?? []) {
    const label = a.values.find((v) => v.value === properties[a.name])?.label
    if (label) return label
  }
  return ''
}

function EndCard({
  label,
  name,
  keyValue,
  typeTitle,
  icon,
}: {
  label: string
  name: string
  keyValue: string
  typeTitle: string
  icon: string | undefined
}) {
  return (
    <div className="carto-end">
      <span className="carto-icon-tile carto-icon-tile-lg">{icon ? <ArchimateIcon svg={icon} className="archimate-icon-lg" /> : null}</span>
      <div className="carto-end-text">
        <span className="carto-filter-label">{label}</span>
        <strong>{name}</strong>
        <span className="mono muted small">{keyValue}</span>
      </div>
      <span className="mono muted small carto-end-type">{typeTitle}</span>
    </div>
  )
}
