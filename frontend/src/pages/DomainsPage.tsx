import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, sessionCapabilities } from '../apiClient'
import type { DomainDataset, DomainDetail } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { NoBranchNote } from '../components/NoBranchNote'
import { useSession } from '../hooks'
import { useWorkspace } from '../workspace'

// Domaines (tickets docflow a7baaaa3 et 36d35a88). Gael, 23/11/2026 : un
// domaine = un modèle sémantique Ossie ; on y regroupe des assets retenus, un
// asset donne un dataset (dérivé, en lecture seule : le travail humain se fait
// sur l'asset, dans la Curation).


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

function DomainList({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('curation:write')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [creating, setCreating] = useState(false)
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
}: {
  datasets: DomainDataset[]
  canWrite: boolean
  pending: boolean
  onRemove: (name: string) => void
}) {
  const { t } = useTranslation()
  return (
    <ul className="list-plain stack stack-tight">
      {datasets.map((d) => (
        <li key={d.name} className="row">
          <strong>{d.name}</strong>
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
          {canWrite ? (
            <button
              type="button"
              className="btn btn-sm"
              disabled={pending}
              aria-label={t('domainRemoveDataset', { name: d.name })}
              onClick={() => onRemove(d.name)}
            >
              {t('domainRemove')}
            </button>
          ) : null}
        </li>
      ))}
    </ul>
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
          {canWrite ? (
            <button type="button" className="btn" onClick={() => setConfirmDelete(true)}>
              {t('domainDelete')}
            </button>
          ) : null}
        </div>
      </div>
      <ErrorList messages={detail.error ? errorMessages(t, detail.error) : messages} />
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
                <DatasetRows datasets={consultable} canWrite={canWrite} pending={remove.isPending} onRemove={remove.mutate} />
              </section>
            ) : null}
            {virtual.length > 0 ? (
              <section className="panel stack stack-tight">
                <h3>{t('domainVirtualTitle', { count: virtual.length })}</h3>
                <p className="muted small">{t('domainVirtualHint')}</p>
                <DatasetRows datasets={virtual} canWrite={canWrite} pending={remove.isPending} onRemove={remove.mutate} />
              </section>
            ) : null}
            <section className="panel stack stack-tight">
              <h3>{t('domainLinksTitle', { count: domain.relationships.length + domain.references.length })}</h3>
              {domain.relationships.length + domain.references.length === 0 ? (
                <p className="muted">{t('domainNoLinks')}</p>
              ) : (
                <ul className="list-plain small">
                  {domain.relationships.map((r) => (
                    <li key={r.name}>
                      {t('domainLinkRelationship', {
                        from: r.from_dataset,
                        field: r.from_columns.join(', '),
                        to: r.to_dataset,
                        cardinality: 'n:1',
                      })}
                    </li>
                  ))}
                  {domain.references.map((r) => (
                    <li key={r.name}>
                      {t('domainLinkReference', {
                        from: r.from_dataset,
                        field: r.field,
                        to: r.to_dataset,
                        cardinality: r.cardinality,
                      })}
                    </li>
                  ))}
                </ul>
              )}
            </section>
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
