import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, type AgentProfileItem, type PrimitiveSpec } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { LabelSlugFields, useLabelSlug } from '../components/LabelSlugFields'
import { CreateButton, CreatePanel } from '../components/CreatePanel'
import { PageHeader, SettingsCrumb } from '../components/PageHeader'

// Profils agents : quelles primitives MCP un agent peut appeler. Calqué sur la
// page des profils utilisateurs, avec les primitives du catalogue à la place des
// capacités. Pour chaque primitive : lecture ou écriture, et la capacité
// humaine qu'elle suppose (un agent connecté pour une personne n'a jamais plus
// que cette personne).

/** Libellé lisible d'une capacité : « Sources — Consulter ». */
function useCapabilityLabel() {
  const { t } = useTranslation()
  return (capability: string) => {
    const [domain] = capability.split(':')
    const action = capability.endsWith(':read')
      ? t('capRead')
      : capability.endsWith(':write')
        ? t('capWrite')
        : t(`cap.${capability.replace(':', '_')}`)
    return `${t(`capDomain.${domain}`)} — ${action}`
  }
}

function PrimitiveGrid({
  catalog,
  selected,
  allRead,
  onToggle,
}: {
  catalog: PrimitiveSpec[]
  selected: Set<string>
  allRead: boolean
  onToggle: (id: string) => void
}) {
  const { t } = useTranslation()
  const capabilityLabel = useCapabilityLabel()
  const domains = [...new Set(catalog.map((p) => p.domain))]
  return (
    <>
      {domains.map((domain) => (
        <fieldset key={domain} className="fieldset stack-tight">
          <legend>{t(`mcpDomain.${domain}`)}</legend>
          {catalog
            .filter((p) => p.domain === domain)
            .map((p) => {
              // « Toutes les lectures » : chaque lecture est donnée par la règle.
              const byRule = allRead && p.access === 'read'
              return (
                <label key={p.id} className="check">
                  <input
                    type="checkbox"
                    checked={byRule || selected.has(p.id)}
                    disabled={byRule}
                    onChange={() => onToggle(p.id)}
                  />
                  <span>
                    {t(`mcpPrim.${p.id}`)}{' '}
                    <span className={p.access === 'read' ? 'badge badge-muted' : 'badge badge-warn'}>
                      {p.access === 'read' ? t('agentProfilesRead') : t('agentProfilesWrite')}
                    </span>{' '}
                    <span className="small muted">
                      {t('agentProfilesSupposes', { capability: capabilityLabel(p.capability) })}
                      {p.person_only ? ` · ${t('agentProfilesPersonOnly')}` : ''}
                    </span>
                  </span>
                </label>
              )
            })}
        </fieldset>
      ))}
    </>
  )
}

function ProfileRow({
  profile,
  open,
  onOpen,
  onDuplicate,
  onDelete,
  deleting,
}: {
  profile: AgentProfileItem
  open: boolean
  onOpen: () => void
  onDuplicate: () => void
  onDelete: () => void
  deleting: boolean
}) {
  const { t } = useTranslation()
  // Suppression confirmée en ligne.
  const [confirming, setConfirming] = useState(false)
  return (
    // Toute la ligne ouvre le profil (modification, ou lecture seule pour le
    // profil livré) ; les boutons d'action arrêtent la propagation.
    <tr
      className={open ? 'row-clickable selected' : 'row-clickable'}
      tabIndex={0}
      aria-label={t('agentProfilesOpenNamed', { name: profile.name })}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && e.target === e.currentTarget) onOpen()
      }}
    >
      <td>
        <span className="link-like">{profile.name}</span>{' '}
        {profile.is_builtin ? <span className="pill">{t('agentProfilesBuiltin')}</span> : null}
        <div className="mono muted small">{profile.slug}</div>
      </td>
      <td>
        {profile.effective.length === 0 ? t('agentProfilesNoPrimitive') : profile.effective.length}
        {profile.all_read ? <span className="small muted"> · {t('agentProfilesAllReadShort')}</span> : null}
      </td>
      <td>
        {t('agentProfilesUsedBy', {
          keys: profile.used_by.keys.length,
          connections: profile.used_by.connections.length,
        })}
      </td>
      <td onClick={(e) => e.stopPropagation()}>
        <div className="actions">
          <button type="button" className="btn btn-sm" onClick={onDuplicate}>
            {t('agentProfilesDuplicate')}
          </button>
          {!profile.is_builtin && !confirming ? (
            <button type="button" className="btn btn-sm btn-danger" onClick={() => setConfirming(true)}>
              {t('agentProfilesDelete')}
            </button>
          ) : null}
        </div>
        {confirming ? (
          <div className="stack stack-tight" role="alert">
            <span>{t('agentProfilesDeleteConfirm', { name: profile.name })}</span>
            <span className="actions">
              <button
                type="button"
                className="btn btn-sm btn-danger"
                disabled={deleting}
                onClick={() => {
                  setConfirming(false)
                  onDelete()
                }}
              >
                {t('agentProfilesConfirmDelete')}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setConfirming(false)}>
                {t('cancel')}
              </button>
            </span>
          </div>
        ) : null}
      </td>
    </tr>
  )
}

/** Le profil livré : sa configuration en lecture seule (il se duplique pour s'adapter). */
function BuiltinPanel({
  profile,
  onClose,
  onDuplicate,
}: {
  profile: AgentProfileItem
  onClose: () => void
  onDuplicate: () => void
}) {
  const { t } = useTranslation()
  return (
    <CreatePanel title={t('agentProfilesBuiltinTitle', { name: profile.name })} onCancel={onClose}>
      <p className="info-note">{t('agentProfilesBuiltinReadOnly')}</p>
      {profile.description ? <p>{profile.description}</p> : null}
      {profile.all_read ? <p className="small muted">{t('agentProfilesAllRead')}</p> : null}
      <span className="field-label">{t('agentProfilesPrimitives')}</span>
      <ul className="list-plain">
        {profile.effective.map((id) => (
          <li key={id}>{t(`mcpPrim.${id}`)}</li>
        ))}
      </ul>
      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={onDuplicate}>
          {t('agentProfilesDuplicate')}
        </button>
      </div>
    </CreatePanel>
  )
}

export function AgentProfilesPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [viewingId, setViewingId] = useState<string | null>(null)
  const pair = useLabelSlug()
  const [description, setDescription] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [allRead, setAllRead] = useState(false)

  const profiles = useQuery({ queryKey: ['agent-profiles'], queryFn: api.listAgentProfiles })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['agent-profiles'] })
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const reset = () => {
    setCreating(false)
    setEditingId(null)
    setViewingId(null)
    pair.reset()
    setDescription('')
    setSelected(new Set())
    setAllRead(false)
  }
  const openCreate = () => {
    setMessages([])
    reset()
    setCreating(true)
  }
  const startEdit = (profile: AgentProfileItem) => {
    setMessages([])
    reset()
    if (profile.is_builtin) {
      setViewingId(profile.id)
      return
    }
    setEditingId(profile.id)
    pair.load(profile.name, profile.slug)
    setDescription(profile.description)
    setSelected(new Set(profile.primitives))
    setAllRead(profile.all_read)
  }
  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const catalog = profiles.data?.catalog ?? []
  // Ce qui est envoyé : les cases cochées à la main, hors lectures données par la règle.
  const body = () => ({
    name: pair.label,
    slug: pair.finalSlug,
    description,
    all_read: allRead,
    primitives: [...selected].filter((id) => !(allRead && catalog.find((p) => p.id === id)?.access === 'read')),
  })
  const afterSave = async () => {
    reset()
    await invalidate()
  }
  const create = useMutation({ mutationFn: () => api.createAgentProfile(body()), onSuccess: afterSave, onError })
  const update = useMutation({
    mutationFn: () => api.updateAgentProfile(editingId as string, body()),
    onSuccess: afterSave,
    onError,
  })
  const duplicate = useMutation({
    mutationFn: (id: string) => api.duplicateAgentProfile(id),
    onSuccess: async () => {
      setViewingId(null)
      await invalidate()
    },
    onError,
  })
  const remove = useMutation({ mutationFn: (id: string) => api.deleteAgentProfile(id), onSuccess: invalidate, onError })

  const list = profiles.data?.profiles ?? []
  const viewing = list.find((p) => p.id === viewingId)
  const granted = allRead
    ? new Set([...selected, ...catalog.filter((p) => p.access === 'read').map((p) => p.id)])
    : selected

  return (
    <main>
      <PageHeader
        breadcrumb={<SettingsCrumb />}
        title={t('agentProfilesTitle')}
        subtitle={t('agentProfilesIntro')}
        actions={
          <CreateButton open={creating || editingId !== null || viewingId !== null} onOpen={openCreate}>
            {t('agentProfilesNew')}
          </CreateButton>
        }
      />
      <ErrorList messages={messages} />
      {viewing ? (
        <div className="page-section">
          <BuiltinPanel
            profile={viewing}
            onClose={reset}
            onDuplicate={() => {
              setMessages([])
              duplicate.mutate(viewing.id)
            }}
          />
        </div>
      ) : null}
      {creating || editingId ? (
        <div className="page-section">
          <CreatePanel
            title={editingId ? t('agentProfilesEditTitle') : t('agentProfilesCreateTitle')}
            onCancel={reset}
          >
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault()
                setMessages([])
                if (editingId) update.mutate()
                else create.mutate()
              }}
            >
              <LabelSlugFields
                idPrefix="agent-profile"
                state={pair}
                labelText={t('agentProfilesName')}
                slugText={t('slugLabel')}
                slugLocked={editingId !== null}
              />
              <div className="field">
                <label className="field-label" htmlFor="agent-profile-description">
                  {t('agentProfilesDescription')}
                </label>
                <input
                  id="agent-profile-description"
                  className="input"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <h3 className="panel-title">{t('agentProfilesPrimitives')}</h3>
              <label className="check">
                <input type="checkbox" checked={allRead} onChange={(e) => setAllRead(e.target.checked)} />
                {t('agentProfilesAllRead')}
              </label>
              {granted.size === 0 ? <p className="muted">{t('agentProfilesNoPrimitive')}</p> : null}
              <PrimitiveGrid catalog={catalog} selected={selected} allRead={allRead} onToggle={toggle} />
              <div className="actions">
                <button type="submit" className="btn btn-primary" disabled={create.isPending || update.isPending}>
                  {editingId ? t('agentProfilesSave') : t('agentProfilesCreate')}
                </button>
              </div>
            </form>
          </CreatePanel>
        </div>
      ) : null}

      <div className="page-body">
        {list.length > 0 ? (
          <div className="panel table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('agentProfilesName')}</th>
                  <th>{t('agentProfilesColPrimitives')}</th>
                  <th>{t('agentProfilesColUsedBy')}</th>
                  <th>{t('colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((profile) => (
                  <ProfileRow
                    key={profile.id}
                    profile={profile}
                    open={profile.id === editingId || profile.id === viewingId}
                    onOpen={() => startEdit(profile)}
                    onDuplicate={() => {
                      setMessages([])
                      duplicate.mutate(profile.id)
                    }}
                    onDelete={() => {
                      setMessages([])
                      remove.mutate(profile.id)
                    }}
                    deleting={remove.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-note">{t('agentProfilesEmpty')}</div>
        )}
      </div>
    </main>
  )
}
