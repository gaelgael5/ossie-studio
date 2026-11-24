import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { CurationAssetProperty, LinkTarget, ReferentielLink } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'

// Lier une propriété d'asset à une propriété de référentiel (ticket 8269c642).
// Gael : « si dans le fichier de schéma référentiel la clé est composite il faut que
// l'IHM oriente très fortement pour obliger à mettre la deuxième clé » ; une propriété
// n'est liée qu'à un seul référentiel. Le serveur revérifie chaque règle.

const REFRESH = ['curationAsset', 'curationAssets', 'draft', 'draftChanges']

export function useLinkTargets(spaceId: string, branchId: string) {
  return useQuery({
    queryKey: ['curationLinkTargets', spaceId, branchId],
    queryFn: () => api.listLinkTargets(spaceId, branchId),
  })
}

function linkLabel(link: ReferentielLink, t: (k: string, o?: Record<string, unknown>) => string): string {
  const base = `${link.referentiel} · ${link.property}`
  if (link.status === 'broken') return `${base} · ${t('linkBroken')}`
  if (link.status === 'partial') return `${base} · ${t('linkKeyCount', { done: link.linked_keys, size: link.key_size })}`
  return base
}

/** Cellule « Référentiel » d'une propriété : étiquette (Modifier, Délier) ou « + Lier ». */
export function ReferentielLinkCell({
  spaceId,
  branchId,
  source,
  path,
  property,
  canWrite,
  onLink,
}: {
  spaceId: string
  branchId: string
  source: string
  path: string
  property: CurationAssetProperty
  canWrite: boolean
  onLink: (property: CurationAssetProperty) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [menu, setMenu] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const link = property.referentiel ?? null
  const unlink = useMutation({
    mutationFn: () => api.unlinkReferentiel(spaceId, branchId, source, path, property.name),
    onSuccess: async () => {
      setConfirming(false)
      setMenu(false)
      await Promise.all(REFRESH.map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
    },
  })
  if (!link) {
    return canWrite ? (
      <button
        type="button"
        className="btn-link"
        aria-label={t('linkAddFor', { name: property.name })}
        onClick={() => onLink(property)}
      >
        {t('linkAdd')}
      </button>
    ) : (
      <span className="muted">—</span>
    )
  }
  const tone = link.status === 'ok' ? 'badge' : link.status === 'partial' ? 'badge badge-warn' : 'badge badge-danger'
  const others = link.members.filter((m) => m !== property.name)
  return (
    <div className="stack link-cell">
      {canWrite ? (
        <button
          type="button"
          className={`${tone} link-badge`}
          title={link.schema}
          aria-expanded={menu}
          onClick={() => setMenu((open) => !open)}
        >
          {linkLabel(link, t)}
        </button>
      ) : (
        <span className={tone} title={link.schema}>
          {linkLabel(link, t)}
        </span>
      )}
      {link.status === 'partial' ? (
        <span className="small text-warn">{t('linkPartialHint', { missing: link.missing.join(', ') })}</span>
      ) : null}
      {link.status === 'broken' ? <span className="small text-danger">{t('linkBrokenHint')}</span> : null}
      {menu && canWrite ? (
        <div className="row" role="menu" aria-label={t('linkMenu')}>
          <button type="button" role="menuitem" className="btn btn-sm" onClick={() => onLink(property)}>
            {t('linkEdit')}
          </button>
          <button
            type="button"
            role="menuitem"
            className="btn btn-sm btn-danger"
            onClick={() => (others.length > 0 ? setConfirming(true) : unlink.mutate())}
          >
            {t('linkRemove')}
          </button>
        </div>
      ) : null}
      {confirming ? (
        <div role="alertdialog" aria-label={t('linkRemoveGroup', { others: others.join(', ') })} className="panel stack">
          <p>{t('linkRemoveGroup', { others: others.join(', ') })}</p>
          <div className="row">
            <button type="button" className="btn" onClick={() => setConfirming(false)}>
              {t('cancelEdit')}
            </button>
            <button type="button" className="btn btn-danger" onClick={() => unlink.mutate()}>
              {t('linkRemoveAll')}
            </button>
          </div>
        </div>
      ) : null}
      <ErrorList messages={unlink.error ? errorMessages(t, unlink.error) : []} />
    </div>
  )
}

/** Popup « Lier à un référentiel ». */
export function LinkReferentielDialog({
  spaceId,
  branchId,
  source,
  path,
  property,
  properties,
  onClose,
}: {
  spaceId: string
  branchId: string
  source: string
  path: string
  property: CurationAssetProperty
  properties: CurationAssetProperty[]
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const current = property.referentiel ?? null
  const targets = useLinkTargets(spaceId, branchId)
  const [name, setName] = useState(current?.referentiel ?? '')
  const [targetProp, setTargetProp] = useState(current?.property ?? '')
  const [companions, setCompanions] = useState<Record<string, string>>(() =>
    current && current.members.length > 1
      ? Object.fromEntries(
          properties
            .filter((p) => current.members.includes(p.name) && p.name !== property.name && p.referentiel)
            .map((p) => [p.referentiel!.property, p.name]),
        )
      : {},
  )
  const target: LinkTarget | undefined = targets.data?.referentiels.find((r) => r.name === name)
  const key = target?.key ?? []
  const composite = key.length > 1 && key.includes(targetProp)
  const others = composite ? key.filter((k) => k !== targetProp) : []
  // Libres : non liées, ou liées dans le groupe qu'on modifie ; jamais la propriété elle-même.
  const freed = new Set(current?.members ?? [])
  const available = properties.filter((p) => p.name !== property.name && (!p.referentiel || freed.has(p.name)))
  const missing = others.filter((k) => !companions[k])
  const ready = target !== undefined && targetProp !== '' && missing.length === 0
  const link = useMutation({
    mutationFn: () =>
      api.linkReferentiel(spaceId, branchId, source, {
        path,
        referentiel: name,
        links: {
          [property.name]: targetProp,
          ...Object.fromEntries(others.map((k) => [companions[k], k])),
        },
        replace: current ? property.name : null,
      }),
    onSuccess: async () => {
      await Promise.all(REFRESH.map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
      onClose()
    },
  })
  return (
    <div role="dialog" aria-label={t('linkDialogTitle')} className="dialog-backdrop">
      <form
        className="panel dialog-panel stack"
        onSubmit={(e) => {
          e.preventDefault()
          if (ready) link.mutate()
        }}
      >
        <div className="row dialog-head">
          <h2>{t('linkDialogTitle')}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <p className="muted small">{t('linkDialogFor', { name: property.business_name || property.name })}</p>
        <ErrorList messages={[targets.error, link.error].flatMap((e) => (e ? errorMessages(t, e) : []))} />
        {targets.data && targets.data.referentiels.length === 0 ? (
          <p className="empty-note">{t('linkNoReferentiel')}</p>
        ) : null}
        <div className="field">
          <label className="field-label" htmlFor="link-referentiel">
            {t('linkReferentiel')}
          </label>
          <select
            id="link-referentiel"
            className="input"
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              setTargetProp('')
              setCompanions({})
            }}
          >
            <option value="">{t('linkChoose')}</option>
            {(targets.data?.referentiels ?? []).map((r) => (
              <option key={r.name} value={r.name}>
                {r.title} ({r.name})
              </option>
            ))}
          </select>
          {target ? <span className="muted mono small">{target.schema}</span> : null}
        </div>
        {target ? (
          <div className="field">
            <label className="field-label" htmlFor="link-property">
              {t('linkProperty')}
            </label>
            <select
              id="link-property"
              className="input"
              value={targetProp}
              onChange={(e) => {
                setTargetProp(e.target.value)
                setCompanions({})
              }}
            >
              <option value="">{t('linkChoose')}</option>
              {target.properties.map((p) => {
                const index = key.indexOf(p.name)
                return (
                  <option key={p.name} value={p.name}>
                    {p.name}
                    {index >= 0 ? ` — ${t('linkKeyBadge', { index: index + 1, size: key.length })}` : ''}
                  </option>
                )
              })}
            </select>
          </div>
        ) : null}
        {composite ? (
          <div className="panel stack link-composite" role="note">
            <p>
              <strong>{t('linkCompositeTitle')}</strong> {t('linkCompositeText', { key: key.join(' + ') })}
            </p>
            {others.map((k) => {
              const sameName = available.filter((p) => p.name === k)
              const rest = available.filter((p) => p.name !== k)
              const chosenElsewhere = new Set(Object.entries(companions).filter(([kk]) => kk !== k).map(([, v]) => v))
              return (
                <div className="field" key={k}>
                  <label className="field-label" htmlFor={`link-companion-${k}`}>
                    {t('linkCompanion', { key: k })}
                  </label>
                  <select
                    id={`link-companion-${k}`}
                    className="input"
                    value={companions[k] ?? ''}
                    onChange={(e) => setCompanions((c) => ({ ...c, [k]: e.target.value }))}
                  >
                    <option value="">{t('linkChoose')}</option>
                    {[...sameName, ...rest]
                      .filter((p) => !chosenElsewhere.has(p.name))
                      .map((p) => (
                        <option key={p.name} value={p.name}>
                          {p.business_name || p.name}
                          {p.name === k ? ` (${t('linkSameName')})` : ''}
                        </option>
                      ))}
                  </select>
                </div>
              )
            })}
          </div>
        ) : null}
        <div className="row modal-foot">
          {!ready && target && targetProp && missing.length > 0 ? (
            <span className="small text-warn">{t('linkWhyDisabled', { missing: missing.join(', ') })}</span>
          ) : null}
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton type="submit" className="btn btn-primary" busy={link.isPending} busyText={t('linkBusy')} disabled={!ready}>
            {t('linkSubmit')}
          </BusyButton>
        </div>
      </form>
    </div>
  )
}
