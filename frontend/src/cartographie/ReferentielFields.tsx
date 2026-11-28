import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { CartoLinkChoices, CartoObjectReferentielLink } from '../apiClient'
import type { Props } from './FoldedProperties'

// Les propriétés liées à un référentiel sur la fiche d'un objet (ticket 43478978) :
// une liste des valeurs du référentiel (une clé composée se choisit d'un coup),
// l'état de la valeur enregistrée et le lien vers elle dans le référentiel. Une
// valeur absente reste affichée telle quelle, marquée : la garder est permis (elle
// est signalée), en saisir une nouvelle absente est refusé par le serveur.

type Link_ = CartoLinkChoices['links'][number]

export function useLinkChoices(spaceId: string, branchId: string, typeSlug: string) {
  return useQuery({
    queryKey: ['carto-link-choices', spaceId, branchId, typeSlug],
    queryFn: () => api.getCartoLinkChoices(spaceId, branchId, typeSlug),
    enabled: Boolean(typeSlug),
  })
}

/** Les propriétés du type que portent des liens (affichées ici, pas dans la liste). */
export function linkedProperties(choices: CartoLinkChoices | undefined): Set<string> {
  return new Set((choices?.links ?? []).flatMap((l) => l.properties))
}

function same(a: unknown, b: unknown): boolean {
  return a === b || (a !== undefined && a !== null && b !== undefined && b !== null && String(a) === String(b))
}

function blank(v: unknown): boolean {
  return v === undefined || v === null || v === ''
}

function selected(link: Link_, values: Props): number | 'absent' | '' {
  if (link.properties.every((p) => blank(values[p]))) return ''
  const index = link.choices.findIndex((c) => link.properties.every((p) => same(values[p], c.value[p])))
  return index >= 0 ? index : 'absent'
}

function currentLabel(link: Link_, values: Props): string {
  return link.properties.map((p) => (blank(values[p]) ? '' : String(values[p]))).join('+')
}

export function ReferentielFields({
  choices,
  values,
  saved,
  detailLinks,
  readOnly,
  onChange,
}: {
  choices: CartoLinkChoices | undefined
  values: Props
  saved: Props
  detailLinks: CartoObjectReferentielLink[]
  readOnly: boolean
  onChange: (next: Props) => void
}) {
  const { t } = useTranslation()
  const links = choices?.links ?? []
  if (links.length === 0) return null
  return (
    <div className="stack carto-ref-fields" aria-label={t('cartoRefValue')}>
      <div className="carto-detail-title">{t('cartoRefValue')}</div>
      {links.map((link) => {
        const choice = selected(link, values)
        const unchanged = link.properties.every((p) => same(values[p], saved[p]) || (blank(values[p]) && blank(saved[p])))
        const status = unchanged ? detailLinks.find((d) => d.referentiel === link.referentiel)?.status : undefined
        const first = link.properties.map((p) => values[p]).find((v) => !blank(v))
        const id = `carto-ref-${link.referentiel}-${link.properties.join('-')}`
        return (
          <div key={id} className="field">
            <label className="field-label" htmlFor={id}>
              {link.properties.join(' + ')}
            </label>
            <select
              id={id}
              className="select"
              value={String(choice)}
              disabled={readOnly}
              onChange={(e) => {
                const next: Props = { ...values }
                if (e.target.value === '') {
                  for (const p of link.properties) delete next[p]
                } else if (e.target.value !== 'absent') {
                  const picked = link.choices[Number(e.target.value)]
                  for (const p of link.properties) next[p] = picked.value[p]
                }
                onChange(next)
              }}
            >
              <option value="">{t('cartoRefChoose')}</option>
              {choice === 'absent' ? (
                <option value="absent">{t('cartoRefAbsentOption', { value: currentLabel(link, values) })}</option>
              ) : null}
              {link.choices.map((c, i) => (
                <option key={c.label} value={String(i)}>
                  {c.hint ? `${c.label} — ${c.hint}` : c.label}
                </option>
              ))}
            </select>
            {link.truncated ? <span className="field-hint">{t('cartoRefTruncated', { count: link.choices.length })}</span> : null}
            {choice === 'absent' ? (
              <span className="field-error">{t('cartoRefValueStatus_absent')}</span>
            ) : status && status !== 'empty' ? (
              <span className={status === 'found' ? 'field-hint' : 'field-error'}>{t(`cartoRefValueStatus_${status}`)}</span>
            ) : null}
            {!blank(first) && choice !== 'absent' ? (
              <Link
                className="small"
                to={`/referentiels/${encodeURIComponent(link.referentiel)}?valeur=${encodeURIComponent(String(first))}`}
              >
                {t('cartoRefValueSee')}
              </Link>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
