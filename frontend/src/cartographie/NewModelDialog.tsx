import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { api } from '../apiClient'
import type { CartoCatalog } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { cartoModelPath } from './ModelsTab'

// Fenêtre « Nouveau modèle » (ticket 5a05c7bb, maquette validée par Gael) : libellé et
// slug (comme la création d'un référentiel), description, fichier de règles
// (`standard` par défaut ; absent, il est créé vide et la règle par défaut
// s'applique), nom et point de vue de la première vue. Les fichiers créés sont
// montrés avant la création ; « Créer et ouvrir » ouvre le modèle.

export const STANDARD_RULES = 'standard'
const MODELS_DIR = 'cartographie/modeles'
const RULES_DIR = 'cartographie/regles'

interface Props {
  spaceId: string
  branchId: string
  catalog: CartoCatalog
  /** Les fichiers de règles de l'espace (slugs). */
  rules: string[]
  /** Slugs des modèles déjà présents : un slug pris est refusé avant l'envoi. */
  taken: Set<string>
  onClose: () => void
}

export function NewModelDialog({ spaceId, branchId, catalog, rules, taken, onClose }: Props) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const pair = useLabelSlug()
  const [description, setDescription] = useState('')
  const [rulesFile, setRulesFile] = useState(STANDARD_RULES)
  const [view, setView] = useState(t('cartoFirstViewDefault'))
  const [viewpoint, setViewpoint] = useState('')
  const label = pair.label.trim()
  const slug = pair.finalSlug
  const slugTaken = slug !== '' && taken.has(slug)
  const ready = Boolean(label && slug && !slugTaken)
  const ruleChoices = [...new Set([STANDARD_RULES, ...rules])]
  const files = slug
    ? [
        `${MODELS_DIR}/${slug}.modele.yaml`,
        `${MODELS_DIR}/${slug}.graphique.yaml`,
        ...(rules.includes(rulesFile) ? [] : [`${RULES_DIR}/${rulesFile}.rules.yaml`]),
      ]
    : []
  const create = useMutation({
    mutationFn: () =>
      api.createCartoModel(spaceId, branchId, { label, slug, description, rules: rulesFile, view, viewpoint }),
    onSuccess: async (created) => {
      await Promise.all(
        ['carto-models', 'draft', 'draftChanges'].map((k) => queryClient.invalidateQueries({ queryKey: [k] })),
      )
      onClose()
      navigate(cartoModelPath(created.slug))
    },
  })
  return (
    <div role="dialog" aria-label={t('cartoNewModelTitle')} className="dialog-backdrop">
      <form
        className="panel dialog-panel stack"
        onSubmit={(e) => {
          e.preventDefault()
          if (ready) create.mutate()
        }}
      >
        <div className="row dialog-head">
          <h2>{t('cartoNewModelTitle')}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={create.error ? errorMessages(t, create.error) : []} />
        <div className="form-grid">
          <LabelSlugFields
            idPrefix="carto-model"
            state={pair}
            labelText={t('cartoModelLabel')}
            slugText={t('cartoModelSlug')}
          />
        </div>
        {slugTaken ? <span className="field-error">{t('cartoModelSlugTaken')}</span> : null}
        <div className="field">
          <label className="field-label" htmlFor="carto-model-description">
            {t('cartoModelDescription')}
          </label>
          <textarea
            id="carto-model-description"
            className="input"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="carto-model-rules">
              {t('cartoModelRulesFile')}
            </label>
            <select
              id="carto-model-rules"
              className="select"
              value={rulesFile}
              onChange={(e) => setRulesFile(e.target.value)}
            >
              {ruleChoices.map((r) => (
                <option key={r} value={r}>
                  {`${r}.rules.yaml`}
                </option>
              ))}
            </select>
            <span className="field-hint">{t('cartoModelRulesHint')}</span>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="carto-model-view">
              {t('cartoModelFirstView')}
            </label>
            <input id="carto-model-view" className="input" value={view} onChange={(e) => setView(e.target.value)} />
            <label className="field-hint carto-model-viewpoint">
              {t('cartoModelViewpoint')}
              <select
                className="select"
                aria-label={t('cartoModelViewpoint')}
                value={viewpoint}
                onChange={(e) => setViewpoint(e.target.value)}
              >
                <option value="">{t('cartoModelViewpointNone')}</option>
                {(catalog.viewpoints ?? []).map((vp) => (
                  <option key={vp.id} value={vp.id}>
                    {vp.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        {files.length > 0 ? (
          <div className="info-note carto-model-files">
            <span>{t('cartoModelFiles')}</span>
            {files.map((f) => (
              <code key={f} className="mono">
                {f}
              </code>
            ))}
          </div>
        ) : null}
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton
            type="submit"
            className="btn btn-primary"
            busy={create.isPending}
            busyText={t('cartoModelCreateBusy')}
            disabled={!ready}
          >
            {t('cartoModelCreate')}
          </BusyButton>
        </div>
      </form>
    </div>
  )
}
