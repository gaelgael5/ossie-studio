import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type { DomainDataset, DomainDetail, DomainField, DomainSummary } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'
import { DomainLinks } from './DomainLinks'
import { ManualDatasetDialog } from './ManualDatasetDialog'
import { LinkReferentielDialog, ReferentielLinkCell } from '../curation/ReferentielLink'
import type { LinkableProperty } from '../curation/ReferentielLink'

// Domaines (tickets docflow a7baaaa3 et 36d35a88). Gael, 23/11/2026 : un
// domaine = un modèle sémantique Ossie ; on y regroupe des assets retenus, un
// asset donne un dataset (dérivé, en lecture seule : le travail humain se fait
// sur l'asset, dans la Curation). Un dataset peut aussi être construit à la main
// (ticket 1c68a20a), et modifié ici : voir `ManualDatasetDialog`.


function useBranch(): { spaceId: string; branchId: string } | null {
  const ws = useWorkspace()
  return ws.spaceId && ws.branchId ? { spaceId: ws.spaceId, branchId: ws.branchId } : null
}

function NoBranch() {
  const { t } = useTranslation()
  const ws = useWorkspace()
  return (
    <main>
      {ws.spaceId ? <NoBranchNote spaceId={ws.spaceId} /> : <div className="empty-note">{t('noSpaceYet')}</div>}
    </main>
  )
}

export function DomainsPage() {
  const branch = useBranch()
  return branch ? <DomainList {...branch} /> : <NoBranch />
}

// « Éditer » un domaine (ticket 82fc14f6). Gael, 24/11/2026 : « Ajoute un bouton
// éditer pour pouvoir modifier le label et la description. » Le slug est montré
// mais pas modifiable : il nomme les fichiers domaines/<slug>.* et le modèle Ossie.
function EditDomainDialog({
  spaceId,
  branchId,
  domain,
  onClose,
}: {
  spaceId: string
  branchId: string
  domain: { name: string; label: string; description: string }
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [label, setLabel] = useState(domain.label)
  const [description, setDescription] = useState(domain.description)
  const save = useMutation({
    mutationFn: () =>
      api.editDomain(spaceId, branchId, domain.name, { label: label.trim(), description: description.trim() }),
    onSuccess: async () => {
      await Promise.all(
        ['domains', 'domain', 'draft', 'draftChanges'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      )
      onClose()
    },
  })
  const title = t('domainEditTitle', { name: domain.label })
  return (
    <div role="dialog" aria-label={title} className="dialog-backdrop">
      <form
        className="panel dialog-panel stack"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <div className="row dialog-head">
          <h2>{title}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={save.error ? errorMessages(t, save.error) : []} />
        <div className="field">
          <label className="field-label" htmlFor="edit-domain-label">
            {t('fieldLabel')}
          </label>
          <input
            id="edit-domain-label"
            className="input"
            autoFocus
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <span className="field-hint">
            {t('fieldSlug')} : <code>{domain.name}</code> — {t('domainSlugFixed')}
          </span>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="edit-domain-desc">
            {t('domainDescription')}
          </label>
          <textarea
            id="edit-domain-desc"
            className="input"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton type="submit" className="btn btn-primary" busy={save.isPending} busyText={t('domainEditBusy')}>
            {t('save')}
          </BusyButton>
        </div>
      </form>
    </div>
  )
}

function DomainList({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('curation:write')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<DomainSummary | null>(null)
  // Règle commune : libellé d'abord, slug déduit et corrigeable (bug 668d0c75).
  const pair = useLabelSlug()
  const [description, setDescription] = useState('')
  const [messages, setMessages] = useState<string[]>([])
  const list = useQuery({
    queryKey: ['domains', spaceId, branchId],
    queryFn: () => api.listDomains(spaceId, branchId),
  })
  const create = useMutation({
    mutationFn: () =>
      api.createDomain(spaceId, branchId, {
        name: pair.finalSlug,
        label: pair.label.trim(),
        description: description.trim(),
      }),
    onMutate: () => setMessages([]),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['domains'] })
      navigate(`/domaines/${encodeURIComponent(created.name)}`)
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const domains = list.data?.domains ?? []
  return (
    <main>
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('domainsTitle')}</h1>
          <p className="page-sub">{t('domainsSubtitle')}</p>
        </div>
        {canWrite ? (
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            {t('domainNew')}
          </button>
        ) : null}
      </div>
      <ErrorList messages={list.error ? errorMessages(t, list.error) : messages} />
      {creating ? (
        <section className="panel stack" aria-label={t('domainNew')}>
          <div className="form-grid">
            <LabelSlugFields
              idPrefix="new-domain"
              state={pair}
              labelText={t('fieldLabel')}
              slugText={t('fieldSlug')}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="new-domain-desc">
              {t('domainDescription')}
            </label>
            <input
              id="new-domain-desc"
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="row">
            <button type="button" className="btn" onClick={() => setCreating(false)}>
              {t('cancelEdit')}
            </button>
            <BusyButton
              type="button"
              className="btn btn-primary"
              busy={create.isPending}
              busyText={t('domainCreateBusy')}
              disabled={!pair.label.trim() || !pair.finalSlug}
              onClick={() => create.mutate()}
            >
              {t('domainCreate')}
            </BusyButton>
          </div>
        </section>
      ) : null}
      {editing ? (
        <EditDomainDialog spaceId={spaceId} branchId={branchId} domain={editing} onClose={() => setEditing(null)} />
      ) : null}
      {list.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {list.data && domains.length === 0 ? (
        <div className="empty-note">
          {t('domainsEmpty')} <Link to="/curation">{t('domainGoCuration')}</Link>
        </div>
      ) : null}
      {domains.length > 0 ? (
        <div className="panel table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('domainName')}</th>
                <th>{t('domainDescription')}</th>
                <th>{t('domainConsultable')}</th>
                <th>{t('domainVirtual')}</th>
                {canWrite ? <th>{t('colActions')}</th> : null}
              </tr>
            </thead>
            <tbody>
              {domains.map((d) => (
                // Toute la ligne ouvre le domaine (convention des listes, demande
                // de Gael du 24/11) ; le lien reste pour le clavier et le clic milieu.
                <tr
                  key={d.name}
                  className="row-clickable"
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest('a')) return
                    navigate(`/domaines/${encodeURIComponent(d.name)}`)
                  }}
                >
                  <td>
                    <Link className="link-strong" to={`/domaines/${encodeURIComponent(d.name)}`}>
                      {d.label}
                    </Link>
                    <div className="muted mono small">{d.name}</div>
                    {d.warnings.length > 0 ? (
                      <span className="text-danger small"> {t('domainWarnings', { count: d.warnings.length })}</span>
                    ) : null}
                  </td>
                  <td>{d.description}</td>
                  <td>{d.consultable}</td>
                  <td>{d.virtual}</td>
                  {canWrite ? (
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm"
                        aria-label={t('domainEditNamed', { name: d.label })}
                        onClick={(e) => {
                          // Le bouton édite, il n'ouvre pas le domaine (ligne cliquable).
                          e.stopPropagation()
                          setEditing(d)
                        }}
                      >
                        {t('domainEdit')}
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </main>
  )
}

export function DomainPage() {
  const branch = useBranch()
  const { name = '' } = useParams()
  return branch ? <DomainScreen {...branch} name={name} /> : <NoBranch />
}

/** Graphe des datasets et de leurs liens : disposition en cercle, sans dépendance. */
function DomainGraph({ domain }: { domain: DomainDetail }) {
  const { t } = useTranslation()
  const n = domain.datasets.length
  if (n === 0) return null
  const size = 420
  const radius = n === 1 ? 0 : size / 2 - 70
  const pos = new Map(
    domain.datasets.map((d, i) => {
      const angle = (2 * Math.PI * i) / n - Math.PI / 2
      return [d.name, { x: size / 2 + radius * Math.cos(angle), y: size / 2 + radius * Math.sin(angle) }]
    }),
  )
  const edges = [
    ...domain.relationships.map((r) => ({ key: `r-${r.name}`, from: r.from_dataset, to: r.to_dataset, rel: true })),
    ...domain.references.map((r) => ({ key: `f-${r.name}`, from: r.from_dataset, to: r.to_dataset, rel: false })),
  ]
  return (
    <svg className="domain-graph" viewBox={`0 0 ${size} ${size}`} role="img" aria-label={t('domainGraph')}>
      {edges.map((e) => {
        const a = pos.get(e.from)
        const b = pos.get(e.to)
        if (!a || !b) return null
        return (
          <line
            key={e.key}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            className={e.rel ? 'domain-edge domain-edge-relationship' : 'domain-edge'}
          />
        )
      })}
      {domain.datasets.map((d) => {
        const p = pos.get(d.name)!
        return (
          <g key={d.name}>
            <circle cx={p.x} cy={p.y} r={22} className={d.consultable ? 'domain-node' : 'domain-node domain-node-virtual'} />
            <text x={p.x} y={p.y + 38} textAnchor="middle">
              {d.name}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function DatasetRows({
  datasets,
  canWrite,
  pending,
  onRemove,
  onEdit,
  where,
  links,
}: {
  datasets: DomainDataset[]
  canWrite: boolean
  pending: boolean
  onRemove: (name: string) => void
  /** Modifier un dataset manuel (un dérivé reste en lecture seule). */
  onEdit: (dataset: DomainDataset) => void
  where: FieldsWhere
  /** Nombre de relations et références d'un dataset : retirer le dataset les
   * retire aussi, l'écran le dit avant de confirmer (ticket 4e856094). */
  links: (name: string) => number
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState<string | null>(null)
  const toggle = (name: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  return (
    <ul className="list-plain stack stack-tight">
      {datasets.map((d) => (
        <li key={d.name} className="stack stack-tight">
          <div className="row">
          <button
            type="button"
            className="btn-link"
            aria-expanded={open.has(d.name)}
            aria-label={t('domainFieldsOf', { name: d.name })}
            onClick={() => toggle(d.name)}
          >
            {open.has(d.name) ? '▾' : '▸'} <strong>{d.name}</strong>
          </button>
          <span className="pill">{d.derived ? t('domainDerived') : t('domainManual')}</span>
          <span className="muted small">{t('domainFieldCount', { count: d.fields.length })}</span>
          {d.asset ? (
            <Link
              className="small"
              to={`/curation?${new URLSearchParams({ source: d.asset.source, path: d.asset.path }).toString()}`}
            >
              {t('domainSeeAsset')}
            </Link>
          ) : null}
          {canWrite && !d.derived ? (
            <button
              type="button"
              className="btn btn-sm"
              aria-label={t('manualEditNamed', { name: d.name })}
              onClick={() => onEdit(d)}
            >
              {t('domainEdit')}
            </button>
          ) : null}
          {canWrite ? (
            <button
              type="button"
              className="btn btn-sm"
              disabled={pending}
              aria-label={t('domainRemoveDataset', { name: d.name })}
              onClick={() => (links(d.name) > 0 ? setConfirming(d.name) : onRemove(d.name))}
            >
              {t('domainRemove')}
            </button>
          ) : null}
          </div>
          {confirming === d.name ? (
            <div className="banner row" role="alertdialog" aria-label={t('domainRemoveDataset', { name: d.name })}>
              <span>{t('domainRemoveWithLinks', { name: d.name, count: links(d.name) })}</span>
              <button type="button" className="btn btn-sm" onClick={() => setConfirming(null)}>
                {t('cancelEdit')}
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => {
                  setConfirming(null)
                  onRemove(d.name)
                }}
              >
                {t('domainRemove')}
              </button>
            </div>
          ) : null}
          {open.has(d.name) ? <DatasetFields dataset={d} canWrite={canWrite} where={where} /> : null}
        </li>
      ))}
    </ul>
  )
}

/** Étiquette d'un lien de référentiel sur un champ de dataset, en lecture seule. */
function FieldLink({ link }: { link: NonNullable<DomainField['referentiel']> }) {
  const { t } = useTranslation()
  const tone = link.status === 'ok' ? 'badge badge-ok' : link.status === 'partial' ? 'badge badge-warn' : 'badge badge-danger'
  const extra =
    link.status === 'partial'
      ? ` · ${t('linkKeyCount', { done: link.linked_keys, size: link.key_size })}`
      : link.status === 'broken'
        ? ` · ${t('linkBroken')}`
        : ''
  return (
    <div className="stack link-cell">
      <span className={tone} title={link.schema}>
        {`${link.referentiel} · ${link.property}${extra}`}
      </span>
      {link.inherited ? <span className="muted small">{t('domainLinkInherited')}</span> : null}
      {link.status === 'partial' ? (
        <span className="small text-warn">{t('linkPartialHint', { missing: link.missing.join(', ') })}</span>
      ) : null}
      {link.status === 'broken' ? <span className="small text-danger">{t('linkBrokenHint')}</span> : null}
    </div>
  )
}

interface FieldsWhere {
  spaceId: string
  branchId: string
  domain: string
}

const DOMAIN_REFRESH = ['domain', 'domains', 'draft', 'draftChanges']

/**
 * Champs d'un dataset : type et lien de référentiel. Dérivé : lien hérité de l'asset,
 * en lecture seule (ticket 65235f64). Manuel : « + Lier », Modifier, Délier, avec la
 * popup et les règles de la Curation (ticket 80bb56af).
 */
function DatasetFields({ dataset, canWrite, where }: { dataset: DomainDataset; canWrite: boolean; where: FieldsWhere }) {
  const { t } = useTranslation()
  const [linking, setLinking] = useState<LinkableProperty | null>(null)
  const { spaceId, branchId, domain } = where
  const editable = canWrite && !dataset.derived
  const linkable: LinkableProperty[] = dataset.fields.map((f) => ({
    name: f.name,
    business_name: '',
    referentiel: f.referentiel ?? null,
  }))
  if (dataset.fields.length === 0) return <p className="muted small">{t('domainNoFields')}</p>
  return (
    <div className="table-wrap">
      <table className="table" aria-label={t('domainFieldsOf', { name: dataset.name })}>
        <thead>
          <tr>
            <th>{t('manualFieldName')}</th>
            <th>{t('manualFieldType')}</th>
            <th>{t('domainReferentiel')}</th>
          </tr>
        </thead>
        <tbody>
          {dataset.fields.map((f, index) => (
            <tr key={f.name}>
              <td className="mono">{f.name}</td>
              <td>{f.datatype || <span className="muted">—</span>}</td>
              <td>
                {editable ? (
                  <ReferentielLinkCell
                    spaceId={spaceId}
                    branchId={branchId}
                    source=""
                    path=""
                    property={linkable[index]}
                    canWrite
                    onLink={setLinking}
                    unlinkFn={() => api.unlinkDomainField(spaceId, branchId, domain, dataset.name, f.name)}
                    refreshKeys={DOMAIN_REFRESH}
                  />
                ) : f.referentiel ? (
                  <FieldLink link={f.referentiel} />
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {linking ? (
        <LinkReferentielDialog
          spaceId={spaceId}
          branchId={branchId}
          source=""
          path=""
          property={linking}
          properties={linkable}
          onClose={() => setLinking(null)}
          submit={(body) => api.linkDomainField(spaceId, branchId, domain, dataset.name, body)}
          refreshKeys={DOMAIN_REFRESH}
        />
      ) : null}
    </div>
  )
}

function DomainScreen({ spaceId, branchId, name }: { spaceId: string; branchId: string; name: string }) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('curation:write')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [messages, setMessages] = useState<string[]>([])
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing] = useState(false)
  // Dataset manuel : `null` fermé, `'new'` création, sinon le dataset modifié.
  const [manual, setManual] = useState<DomainDataset | 'new' | null>(null)
  const detail = useQuery({
    queryKey: ['domain', spaceId, branchId, name],
    queryFn: () => api.getDomain(spaceId, branchId, name),
  })
  const refresh = () =>
    Promise.all(
      ['domain', 'domains', 'draft', 'draftChanges'].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    )
  const remove = useMutation({
    mutationFn: (dataset: string) => api.removeDomainDataset(spaceId, branchId, name, dataset),
    onMutate: () => setMessages([]),
    onSuccess: refresh,
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const drop = useMutation({
    mutationFn: () => api.deleteDomain(spaceId, branchId, name),
    onSuccess: async () => {
      await refresh()
      navigate('/domaines')
    },
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const domain = detail.data
  const consultable = domain?.datasets.filter((d) => d.consultable) ?? []
  const virtual = domain?.datasets.filter((d) => !d.consultable) ?? []
  const linksOf = (dataset: string) =>
    [...(domain?.relationships ?? []), ...(domain?.references ?? [])].filter(
      (l) => l.from_dataset === dataset || l.to_dataset === dataset,
    ).length
  return (
    <main className="stack">
      <div className="breadcrumb">
        <Link to="/domaines">{t('domainsTitle')}</Link> › {domain?.label ?? name}
      </div>
      <div className="page-head">
        <div>
          <h1 className="page-title">{domain?.label ?? name}</h1>
          <p className="muted mono small">{name}</p>
          {domain?.description ? <p className="page-sub">{domain.description}</p> : null}
        </div>
        <div className="row">
          <Link className="btn" to="/curation">
            {t('domainAddFromCuration')}
          </Link>
          {canWrite && domain ? (
            <button type="button" className="btn btn-primary" onClick={() => setManual('new')}>
              {t('manualNew')}
            </button>
          ) : null}
          {canWrite && domain ? (
            <button type="button" className="btn" onClick={() => setEditing(true)}>
              {t('domainEdit')}
            </button>
          ) : null}
          {canWrite ? (
            <button type="button" className="btn" onClick={() => setConfirmDelete(true)}>
              {t('domainDelete')}
            </button>
          ) : null}
        </div>
      </div>
      <ErrorList messages={detail.error ? errorMessages(t, detail.error) : messages} />
      {editing && domain ? (
        <EditDomainDialog spaceId={spaceId} branchId={branchId} domain={domain} onClose={() => setEditing(false)} />
      ) : null}
      {manual && domain ? (
        <ManualDatasetDialog
          spaceId={spaceId}
          branchId={branchId}
          domain={name}
          datatypes={domain.datatypes ?? []}
          dataset={manual === 'new' ? undefined : manual}
          onClose={() => setManual(null)}
        />
      ) : null}
      {confirmDelete ? (
        <div className="banner row" role="alertdialog" aria-label={t('domainDelete')}>
          <span>{t('domainDeleteConfirm', { name })}</span>
          <button type="button" className="btn btn-sm" onClick={() => setConfirmDelete(false)}>
            {t('cancelEdit')}
          </button>
          <button type="button" className="btn btn-sm btn-primary" onClick={() => drop.mutate()}>
            {t('domainDelete')}
          </button>
        </div>
      ) : null}
      {detail.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {domain && domain.datasets.length === 0 ? (
        <div className="empty-note">
          {t('domainEmpty')} <Link to="/curation">{t('domainGoCuration')}</Link>
        </div>
      ) : null}
      {domain && domain.datasets.length > 0 ? (
        <div className="curation-gov">
          <div className="stack">
            {consultable.length > 0 ? (
              <section className="panel stack stack-tight">
                <h3>{t('domainConsultableTitle', { count: consultable.length })}</h3>
                <DatasetRows datasets={consultable} canWrite={canWrite} pending={remove.isPending} onRemove={remove.mutate} onEdit={setManual} where={{ spaceId, branchId, domain: name }} links={linksOf} />
              </section>
            ) : null}
            {virtual.length > 0 ? (
              <section className="panel stack stack-tight">
                <h3>{t('domainVirtualTitle', { count: virtual.length })}</h3>
                <p className="muted small">{t('domainVirtualHint')}</p>
                <DatasetRows datasets={virtual} canWrite={canWrite} pending={remove.isPending} onRemove={remove.mutate} onEdit={setManual} where={{ spaceId, branchId, domain: name }} links={linksOf} />
              </section>
            ) : null}
            <DomainLinks spaceId={spaceId} branchId={branchId} domain={domain} canWrite={canWrite} />
          </div>
          <section className="panel stack stack-tight">
            <h3>{t('domainGraph')}</h3>
            <DomainGraph domain={domain} />
            <p className="muted small">{t('domainGraphLegend')}</p>
          </section>
        </div>
      ) : null}
    </main>
  )
}
