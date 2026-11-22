import { createContext, useContext, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, sessionCapabilities } from '../apiClient'
import type { RawObjectDetail } from '../apiClient'
import { errorMessages } from '../errors'
import { EnrichPanel } from '../components/EnrichPanel'
import { ErrorList } from '../components/ErrorList'
import { useSession } from '../hooks'
import { EyeIcon } from './RawPreview'

// Gestion de l'asset depuis l'explorateur raw (demande de Gael, 22/11/2026) :
// créer l'asset d'un objet, le retirer, et choisir les champs exposés d'un clic
// sur l'œil — sans passer par la page Curation. Mêmes appels que la curation
// (retain / remove) : l'asset s'écrit dans le brouillon, capacité curation:write.
// Créer un asset retient TOUS les champs (comme « Retenir tout ») : on masque
// ensuite ceux qu'on ne veut pas, d'un clic sur l'œil.

interface AssetEditor {
  canWrite: boolean
  retained: boolean
  pending: boolean
  create: () => void
  /** Enfants (objets référencés par les propriétés, récursivement) pas encore retenus. */
  children: { slug: string; file: string; title: string }[]
  createWithChildren: () => void
  remove: () => void
  toggle: (field: string) => void
}

const AssetEditorContext = createContext<AssetEditor | null>(null)

export function AssetEditorProvider({
  spaceId,
  branchId,
  slug,
  detail,
  children,
}: {
  spaceId: string
  branchId: string
  slug: string
  detail: RawObjectDetail
  children: ReactNode
}) {
  const { t } = useTranslation()
  const session = useSession()
  const canWrite = sessionCapabilities(session.data).includes('curation:write')
  const queryClient = useQueryClient()
  const [messages, setMessages] = useState<string[]>([])
  const refresh = async () => {
    setMessages([])
    await Promise.all(
      ['rawObject', 'rawFolder', 'rawSearch', 'curation'].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    )
  }
  const onError = (err: unknown) => setMessages(errorMessages(t, err))
  const retain = useMutation({
    mutationFn: (fields: string[] | null) =>
      api.retainAsset(spaceId, branchId, slug, detail.file, fields),
    onSuccess: refresh,
    onError,
  })
  // Enfants de l'objet, pour « ajouter avec ses enfants » : chargés seulement
  // quand l'objet n'est pas encore retenu (c'est là que le menu sert).
  const descendants = useQuery({
    queryKey: ['rawDescendants', spaceId, branchId, slug, detail.file],
    queryFn: () => api.getRawDescendants(spaceId, branchId, slug, detail.file),
    enabled: canWrite && !detail.exposure.retained,
    staleTime: 30_000,
    retry: false,
  })
  const pendingChildren = (descendants.data?.descendants ?? []).filter((d) => !d.retained)
  const retainFamily = useMutation({
    // Un commit par source : l'objet et ses enfants de la même source ensemble,
    // les enfants d'une autre source dans la leur.
    mutationFn: async () => {
      const bySlug = new Map<string, string[]>([[slug, [detail.file]]])
      for (const child of pendingChildren) {
        bySlug.set(child.slug, [...(bySlug.get(child.slug) ?? []), child.file])
      }
      for (const [source, paths] of bySlug) {
        await api.retainAssets(spaceId, branchId, source, paths)
      }
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['rawDescendants'] })
      await refresh()
    },
    onError,
  })
  const drop = useMutation({
    mutationFn: () => api.removeAsset(spaceId, branchId, slug, detail.file),
    onSuccess: refresh,
    onError,
  })
  const exposed = detail.fields.filter((f) => f.exposed).map((f) => f.name)
  const editor: AssetEditor = {
    canWrite,
    retained: detail.exposure.retained,
    pending: retain.isPending || drop.isPending || retainFamily.isPending,
    create: () => retain.mutate(null),
    children: pendingChildren,
    createWithChildren: () => retainFamily.mutate(),
    remove: () => drop.mutate(),
    toggle: (field) =>
      retain.mutate(
        exposed.includes(field) ? exposed.filter((f) => f !== field) : [...exposed, field],
      ),
  }
  return (
    <AssetEditorContext.Provider value={editor}>
      <ErrorList messages={messages} />
      {children}
    </AssetEditorContext.Provider>
  )
}

/** Œil d'un champ : un bouton quand l'asset existe et qu'on peut l'écrire, un indicateur sinon. */
export function EyeCell({ name, exposed }: { name: string; exposed: boolean }) {
  const { t } = useTranslation()
  const editor = useContext(AssetEditorContext)
  if (!editor?.canWrite || !editor.retained) return <EyeIcon exposed={exposed} />
  return (
    <button
      type="button"
      className="raw-eye-btn"
      aria-pressed={exposed}
      aria-label={t(exposed ? 'rawHideField' : 'rawExposeField', { name })}
      title={t(exposed ? 'rawHideField' : 'rawExposeField', { name })}
      disabled={editor.pending}
      onClick={() => editor.toggle(name)}
    >
      <EyeIcon exposed={exposed} />
    </button>
  )
}

/** « Créer un asset », ou « Retirer l'asset » avec confirmation. */
export function AssetButton() {
  const { t } = useTranslation()
  const editor = useContext(AssetEditorContext)
  const [confirming, setConfirming] = useState(false)
  const [choosing, setChoosing] = useState(false)
  if (!editor?.canWrite) return null
  if (!editor.retained) {
    // Sans enfant à ajouter, le bouton crée l'asset directement.
    if (editor.children.length === 0) {
      return (
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={editor.pending}
          onClick={editor.create}
        >
          {t('rawCreateAsset')}
        </button>
      )
    }
    return (
      <span className="raw-asset-menu">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          aria-expanded={choosing}
          disabled={editor.pending}
          onClick={() => setChoosing((v) => !v)}
        >
          {t('rawCreateAsset')}
          {' ▾'}
        </button>
        {choosing ? (
          <span className="raw-asset-menu-popup" role="menu">
            <button
              type="button"
              role="menuitem"
              className="raw-ref-item"
              onClick={() => {
                setChoosing(false)
                editor.create()
              }}
            >
              <span className="raw-ref-title">{t('rawCreateAssetAlone')}</span>
            </button>
            <button
              type="button"
              role="menuitem"
              className="raw-ref-item"
              title={editor.children.map((c) => c.title).join(', ')}
              onClick={() => {
                setChoosing(false)
                editor.createWithChildren()
              }}
            >
              <span className="raw-ref-title">
                {t('rawCreateAssetWithChildren', { count: editor.children.length })}
              </span>
              <span className="small muted">
                {editor.children
                  .slice(0, 5)
                  .map((c) => c.title)
                  .join(', ')}
                {editor.children.length > 5 ? '…' : ''}
              </span>
            </button>
          </span>
        ) : null}
      </span>
    )
  }
  if (!confirming) {
    return (
      <button type="button" className="btn btn-sm" onClick={() => setConfirming(true)}>
        {t('rawRemoveAsset')}
      </button>
    )
  }
  return (
    <span className="raw-asset-confirm" role="alertdialog" aria-label={t('rawRemoveAsset')}>
      <span className="small">{t('rawRemoveAssetConfirm')}</span>{' '}
      <button
        type="button"
        className="btn btn-sm btn-danger"
        disabled={editor.pending}
        onClick={() => {
          setConfirming(false)
          editor.remove()
        }}
      >
        {t('rawRemoveAssetYes')}
      </button>{' '}
      <button type="button" className="btn btn-sm" onClick={() => setConfirming(false)}>
        {t('cancelEdit')}
      </button>
    </span>
  )
}

/** Vrai quand l'œil est cliquable (pour adapter la légende). */
export function useCanEditAsset(): boolean {
  const editor = useContext(AssetEditorContext)
  return !!editor?.canWrite
}

/**
 * Onglet « Enrichissement » de l'aperçu : le panneau de la page Curation (nom
 * métier, description métier, concept, étiquettes) appliqué à l'asset de
 * l'objet. Il faut un asset : sinon, l'onglet invite à le créer.
 */
export function EnrichTab({
  spaceId,
  branchId,
  slug,
  file,
}: {
  spaceId: string
  branchId: string
  slug: string
  file: string
}) {
  const { t } = useTranslation()
  const editor = useContext(AssetEditorContext)
  const queryClient = useQueryClient()
  const [version, setVersion] = useState(0)
  const [notice, setNotice] = useState('')
  if (!editor?.retained) return <p className="muted">{t('rawEnrichNeedsAsset')}</p>
  if (!editor.canWrite) return <p className="muted">{t('rawEnrichReadonly')}</p>
  return (
    <div className="stack">
      {notice ? <p role="status">{notice}</p> : null}
      <EnrichPanel
        key={version}
        spaceId={spaceId}
        branchId={branchId}
        slug={slug}
        path={file}
        onClose={() => {
          setNotice('')
          setVersion((v) => v + 1)
        }}
        onSaved={() => {
          setNotice(t('rawEnrichSaved'))
          void Promise.all(
            ['enrichment', 'rawObject', 'curation'].map((key) =>
              queryClient.invalidateQueries({ queryKey: [key] }),
            ),
          )
        }}
      />
    </div>
  )
}
