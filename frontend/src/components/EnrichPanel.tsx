import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { EnrichScope, EnrichValues } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from './ErrorList'

// Enrichissement d'un asset (clés x- humaines : nom métier, description métier,
// concept, étiquettes), sur l'objet et chacun de ses champs. Partagé par la page
// Curation et l'aperçu de l'explorateur raw (où l'asset se gère désormais).

type ScopeForm = { business_name: string; description: string; concept: string; tags: string }

function _toValues(form: ScopeForm): EnrichValues {
  return {
    business_name: form.business_name,
    description: form.description,
    concept: form.concept,
    tags: form.tags
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
  }
}

function _initScope(scope: EnrichScope): ScopeForm {
  return {
    business_name: scope.business_name,
    description: scope.description,
    concept: scope.concept,
    tags: scope.tags.join(', '),
  }
}

export function EnrichPanel({
  spaceId,
  branchId,
  slug,
  path,
  onClose,
  onSaved,
}: {
  spaceId: string
  branchId: string
  slug: string
  path: string
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const [form, setForm] = useState<Record<string, ScopeForm> | null>(null)
  const [messages, setMessages] = useState<string[]>([])
  const view = useQuery({
    queryKey: ['enrichment', spaceId, branchId, slug, path],
    queryFn: () => api.getEnrichment(spaceId, branchId, slug, path),
    retry: false,
  })

  const scopes: EnrichScope[] = view.data ? [view.data.object, ...view.data.fields] : []
  const current: Record<string, ScopeForm> =
    form ?? Object.fromEntries(scopes.map((s) => [s.name, _initScope(s)]))

  const save = useMutation({
    mutationFn: () =>
      api.enrichAsset(spaceId, branchId, slug, {
        path,
        obj: current[''] ? _toValues(current['']) : null,
        fields: Object.fromEntries(
          (view.data?.fields ?? []).map((f) => [f.name, _toValues(current[f.name])]),
        ),
      }),
    onSuccess: () => {
      setMessages([])
      onSaved()
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })

  const update = (name: string, key: keyof ScopeForm, value: string) =>
    setForm({ ...current, [name]: { ...current[name], [key]: value } })

  const editor = (scope: EnrichScope, label: string) => {
    const f = current[scope.name]
    const readonlyKeys = Object.keys(scope.readonly)
    return (
      <div key={scope.name || '(object)'} className="panel stack stack-tight">
        <p className="panel-label">{label}</p>
        <div className="stack stack-tight">
          <input
            className="input"
            value={f.business_name}
            onChange={(e) => update(scope.name, 'business_name', e.target.value)}
            placeholder={t('enrichBusinessName')}
            aria-label={t('enrichBusinessName')}
          />
          <input
            className="input"
            value={f.description}
            onChange={(e) => update(scope.name, 'description', e.target.value)}
            placeholder={t('enrichDescription')}
            aria-label={t('enrichDescription')}
          />
          <input
            className="input"
            value={f.concept}
            onChange={(e) => update(scope.name, 'concept', e.target.value)}
            placeholder={t('enrichConcept')}
            aria-label={t('enrichConcept')}
          />
          <input
            className="input"
            value={f.tags}
            onChange={(e) => update(scope.name, 'tags', e.target.value)}
            placeholder={t('enrichTags')}
            aria-label={t('enrichTags')}
          />
        </div>
        {readonlyKeys.length > 0 ? (
          <p className="muted small">
            {t('enrichReadonly')}: {readonlyKeys.join(', ')}
          </p>
        ) : null}
      </div>
    )
  }

  return (
    <div className="panel stack">
      <p className="panel-label">{t('enrichTitle', { title: view.data?.title ?? path })}</p>
      <ErrorList messages={messages} />
      {view.data ? (
        <>
          {editor(view.data.object, t('enrichObjectScope'))}
          {view.data.fields.map((field) => editor(field, field.name))}
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
              {t('enrichSave')}
            </button>
            <button type="button" className="btn" onClick={onClose}>
              {t('cancel')}
            </button>
          </div>
        </>
      ) : (
        <p className="muted">{t('loading')}</p>
      )}
    </div>
  )
}

