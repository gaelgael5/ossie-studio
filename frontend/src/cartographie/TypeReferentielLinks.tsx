import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { CartoReferentielInfo, CartoTypeLinks } from '../apiClient'
import { ApiRequestError, errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'

// « Liens vers les référentiels » d'un type de cartographie (ticket 43478978), dans
// l'onglet « Propriétés ». Gael : « à quel endroit dans la page de paramétrage du
// type je suis capable de dire qu'il y a une contrainte ? ». Un lien associe des
// propriétés du type à la clé d'un référentiel (une propriété par partie de la clé,
// de même type) ; il s'écrit dans le fichier d'extension du type, dans le brouillon,
// dès qu'on l'ajoute ou le retire. Une valeur absente du référentiel est alors
// refusée sur la fiche d'un objet et par l'agent, taguée à l'import.

type LinkBody = { referentiel: string; proprietes: Record<string, string> }

const QUERY = 'carto-type-links'

export function TypeReferentielLinks({
  spaceId,
  branchId,
  slug,
  readOnly,
}: {
  spaceId: string
  branchId: string
  slug: string
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const links = useQuery({
    queryKey: [QUERY, spaceId, branchId, slug],
    queryFn: () => api.getCartoTypeLinks(spaceId, branchId, slug),
  })
  const save = useMutation({
    mutationFn: (next: LinkBody[]) => api.saveCartoTypeLinks(spaceId, branchId, slug, next),
    onSuccess: async (data) => {
      queryClient.setQueryData([QUERY, spaceId, branchId, slug], data)
      await Promise.all(
        ['carto-object', 'carto-link-choices', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
    },
  })
  // Une réponse incomplète se lit comme vide (aucun lien, aucun référentiel).
  const data: CartoTypeLinks | undefined = links.data
    ? {
        ...links.data,
        links: links.data.links ?? [],
        referentiels: links.data.referentiels ?? [],
        properties: links.data.properties ?? [],
      }
    : undefined
  const current: LinkBody[] = (data?.links ?? []).map((l) => ({ referentiel: l.referentiel, proprietes: l.proprietes }))
  const problems =
    save.error instanceof ApiRequestError && Array.isArray(save.error.body.error_args?.problems)
      ? (save.error.body.error_args.problems as { code: string; field: string; detail: string }[])
      : []
  return (
    <section className="panel stack carto-ref-links" aria-label={t('cartoRefLinks')}>
      <h3>{t('cartoRefLinks')}</h3>
      <p className="field-hint">{t('cartoRefLinksHelp')}</p>
      <ErrorList messages={links.error ? errorMessages(t, links.error) : []} />
      {links.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {data && data.links.length === 0 ? <p className="muted">{t('cartoRefLinksNone')}</p> : null}
      {data ? (
        <ul className="plain-list stack">
          {data.links.map((link, index) => (
            <li key={link.referentiel + Object.keys(link.proprietes).join(',')} className="carto-ref-link">
              <div className="row">
                <Link to={`/referentiels/${encodeURIComponent(link.referentiel)}`} className="link-strong">
                  {link.target?.title ?? link.referentiel}
                </Link>
                {(link.target?.tags ?? []).map((tag) => (
                  <span key={tag} className="badge badge-muted">
                    {tag}
                  </span>
                ))}
                {!readOnly ? (
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={save.isPending}
                    onClick={() => save.mutate(current.filter((_c, j) => j !== index))}
                  >
                    {t('cartoRefLinkRemove')}
                  </button>
                ) : null}
              </div>
              <ul className="plain-list small">
                {Object.entries(link.proprietes).map(([prop, target]) => (
                  <li key={prop}>
                    <code>{prop}</code> → <code>{target}</code>
                  </li>
                ))}
              </ul>
              {link.absent > 0 ? <p className="text-warn small">{t('cartoRefLinkAbsent', { count: link.absent })}</p> : null}
              {(link.problems ?? []).map((p, i) => (
                <p key={i} className="text-danger small">
                  {p.field ? `${p.field} · ` : ''}
                  {t(`cartoProblem_${p.code}`, { defaultValue: p.code })}
                  {p.detail ? ` (${p.detail})` : ''}
                </p>
              ))}
            </li>
          ))}
        </ul>
      ) : null}
      {data && !readOnly ? (
        <AddLink data={data} busy={save.isPending} onAdd={(link) => save.mutate([...current, link])} />
      ) : null}
      <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
      {problems.length > 0 ? (
        <ul className="carto-problems">
          {problems.map((p, i) => (
            <li key={i} className="mono small">
              {p.field ? `${p.field} · ` : ''}
              {t(`cartoProblem_${p.code}`, { defaultValue: p.code })}
              {p.detail ? ` (${p.detail})` : ''}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

function AddLink({ data, busy, onAdd }: { data: CartoTypeLinks; busy: boolean; onAdd: (link: LinkBody) => void }) {
  const { t } = useTranslation()
  const [tag, setTag] = useState('')
  const [name, setName] = useState('')
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const taken = new Set(data.links.flatMap((l) => Object.keys(l.proprietes)))
  const tags = [...new Set(data.referentiels.flatMap((r) => r.tags))].sort((a, b) => a.localeCompare(b))
  const choices = data.referentiels.filter((r) => !r.error && r.key.length > 0 && (!tag || r.tags.includes(tag)))
  const target: CartoReferentielInfo | undefined = data.referentiels.find((r) => r.name === name)
  const complete = target !== undefined && target.key.every((k) => mapping[k.name])
  const add = () => {
    if (!target || !complete) return
    // Le fichier : propriété du type → propriété de la clé, dans l'ordre de la clé.
    const proprietes: Record<string, string> = {}
    for (const k of target.key) proprietes[mapping[k.name]] = k.name
    onAdd({ referentiel: target.name, proprietes })
    setName('')
    setMapping({})
  }
  return (
    <fieldset className="ref-fieldset stack" aria-label={t('cartoRefLinkAdd')}>
      <legend>{t('cartoRefLinkAdd')}</legend>
      <div className="row">
        {tags.length > 0 ? (
          <select className="input" aria-label={t('refTagFilter')} value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">{t('refTagFilterAll')}</option>
            {tags.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
        ) : null}
        <select
          className="input"
          aria-label={t('cartoRefLinkReferentiel')}
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setMapping({})
          }}
        >
          <option value="">{t('cartoRefLinkChoose')}</option>
          {choices.map((r) => (
            <option key={r.name} value={r.name}>
              {r.title}
              {r.tags.length > 0 ? ` (${r.tags.join(', ')})` : ''}
            </option>
          ))}
        </select>
      </div>
      {data.referentiels.length === 0 ? <p className="muted small">{t('cartoRefLinkNoReferentiel')}</p> : null}
      {target ? (
        <div className="stack">
          <p className="field-hint">{t(target.key.length > 1 ? 'cartoRefLinkComposite' : 'cartoRefLinkSimple')}</p>
          {target.key.map((k) => {
            const usable = data.properties.filter((p) => p.type === k.type && !taken.has(p.name))
            return (
              <label key={k.name} className="row small">
                <span>
                  {t('cartoRefLinkKeyPart')} <code>{k.name}</code> ({k.type})
                </span>
                <select
                  className="input"
                  aria-label={t('cartoRefLinkPropertyFor', { key: k.name })}
                  value={mapping[k.name] ?? ''}
                  onChange={(e) => setMapping((prev) => ({ ...prev, [k.name]: e.target.value }))}
                >
                  <option value="">{t('cartoRefLinkProperty')}</option>
                  {usable.map((p) => (
                    <option key={p.name} value={p.name} disabled={Object.entries(mapping).some(([kk, v]) => kk !== k.name && v === p.name)}>
                      {p.title === p.name ? p.name : `${p.title} (${p.name})`}
                    </option>
                  ))}
                </select>
                {usable.length === 0 ? <span className="muted">{t('cartoRefLinkNoProperty', { type: k.type })}</span> : null}
              </label>
            )
          })}
          <div>
            <BusyButton type="button" className="btn btn-primary" busy={busy} busyText={t('refSaveBusy')} disabled={!complete} onClick={add}>
              {t('cartoRefLinkDo')}
            </BusyButton>
          </div>
        </div>
      ) : null}
    </fieldset>
  )
}
