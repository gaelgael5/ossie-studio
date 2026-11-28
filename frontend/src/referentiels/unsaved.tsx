import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'
import { UNSAFE_DataRouterContext, useBlocker } from 'react-router-dom'
import { BusyButton } from '../components/BusyButton'

// Modifications non enregistrées d'un écran (bug 5a6b0213, Gael : « quand on
// switch d'onglet on perd les modifications ; si l'utilisateur essaie de sortir
// de l'écran sans sauver, demande s'il faut sauver avant de quitter »).
// Chaque onglet déclare ce qu'il a de modifié et comment l'enregistrer ; la garde
// de sortie interroge ce registre au moment de quitter.

export interface UnsavedState {
  label: string
  dirty: boolean
  /** Faux quand l'onglet ne peut pas être enregistré en l'état (problèmes, YAML illisible). */
  saveable: boolean
}

interface Registry {
  entries: Record<string, UnsavedState>
  /** Même registre, tenu à jour sans attendre un rendu : la garde le lit au
   * moment de la navigation (un écran qui vient d'enregistrer puis navigue
   * aussitôt ne doit pas être retenu — bug 62a8c315, objet créé deux fois). */
  live: React.MutableRefObject<Record<string, UnsavedState>>
  set: (id: string, state: UnsavedState | null) => void
  saves: React.MutableRefObject<Record<string, () => Promise<unknown>>>
  /** Abandon des modifications (retour à la version enregistrée), pour « Continuer
   * sans enregistrer » d'un changement d'onglet (bug 9cf80cdb). */
  discards: React.MutableRefObject<Record<string, () => void>>
}

const UnsavedContext = createContext<Registry | null>(null)

export function UnsavedProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<Record<string, UnsavedState>>({})
  const live = useRef<Record<string, UnsavedState>>({})
  const saves = useRef<Record<string, () => Promise<unknown>>>({})
  const discards = useRef<Record<string, () => void>>({})
  const set = useCallback((id: string, state: UnsavedState | null) => {
    const prev = live.current
    let next: Record<string, UnsavedState>
    if (state === null) {
      if (!(id in prev)) return
      next = { ...prev }
      delete next[id]
    } else {
      const old = prev[id]
      if (old && old.dirty === state.dirty && old.saveable === state.saveable && old.label === state.label) return
      next = { ...prev, [id]: state }
    }
    live.current = next
    setEntries(next)
  }, [])
  const value = useMemo(() => ({ entries, live, set, saves, discards }), [entries, set])
  // La garde de navigation a besoin d'un routeur de données (useBlocker) ; sous
  // un MemoryRouter (tests), seule l'alerte du navigateur reste.
  const dataRouter = useContext(UNSAFE_DataRouterContext)
  return (
    <UnsavedContext.Provider value={value}>
      {children}
      {dataRouter ? <LeaveGuard /> : <BeforeUnload />}
    </UnsavedContext.Provider>
  )
}

/** Déclare l'état d'un onglet ; `save` est relu au moment d'enregistrer.
 * `discard` ramène l'onglet à la version enregistrée (sans lui, l'abandon compte
 * sur le démontage de l'onglet par le changement qui suit).
 * Rend `release` : à appeler juste après un enregistrement réussi suivi d'une
 * navigation immédiate (création), pour que la garde ne la retienne pas. */
export function useUnsaved(
  id: string,
  state: UnsavedState,
  save: () => Promise<unknown>,
  discard?: () => void,
): () => void {
  const registry = useContext(UnsavedContext)
  const set = registry?.set
  const saves = registry?.saves
  const discards = registry?.discards
  useEffect(() => {
    if (saves) saves.current[id] = save
    if (discards) {
      if (discard) discards.current[id] = discard
      else delete discards.current[id]
    }
  })
  useEffect(() => {
    set?.(id, state)
  }, [set, id, state.label, state.dirty, state.saveable]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(
    () => () => {
      set?.(id, null)
      if (saves) delete saves.current[id]
      if (discards) delete discards.current[id]
    },
    [set, saves, discards, id],
  )
  return () => set?.(id, { ...state, dirty: false })
}

/**
 * Cmd+S / Ctrl+S (ticket : « intercepter command+S et CTRL+S pour enregistrer le
 * modèle du référentiel ») : enregistre les onglets modifiés dont l'identifiant
 * commence par `prefix()` (l'onglet affiché). Toujours intercepté sur l'écran,
 * même sans rien à enregistrer : le navigateur ne propose pas « Enregistrer sous ».
 * Écoute en phase de capture : l'éditeur YAML (Monaco) ne l'avale pas.
 */
export function useSaveShortcut(prefix: () => string) {
  const registry = useContext(UnsavedContext)
  const latest = useRef({ registry, prefix })
  latest.current = { registry, prefix }
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.key.toLowerCase() !== 's') return
      e.preventDefault()
      const { registry: reg, prefix: current } = latest.current
      if (!reg) return
      const wanted = current()
      for (const [id, state] of Object.entries(reg.entries)) {
        if (wanted && id.startsWith(wanted) && state.dirty && state.saveable) {
          // L'erreur éventuelle est montrée par l'onglet (même mutation que son bouton).
          reg.saves.current[id]?.().catch(() => undefined)
        }
      }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [])
}

/** Onglets modifiés (identifiants commençant par `prefix`, ou tous). */
export function useDirty(prefix = ''): UnsavedState[] {
  const registry = useContext(UnsavedContext)
  return Object.entries(registry?.entries ?? {})
    .filter(([id, s]) => s.dirty && id.startsWith(prefix))
    .map(([, s]) => s)
}

/**
 * Changement d'onglet (ou d'import) qui dépend de modifications non enregistrées
 * (bug 9cf80cdb, Gael : « on a la sensation qu'on est dans le même écran. si le fait
 * de cliquer sur les onglets nécessite d'enregistrer, il faut la popup »).
 * `guard(prefixes, go)` : sans onglet modifié dont l'identifiant commence par l'un
 * des préfixes, `go` tout de suite ; sinon la fenêtre Rester / Continuer sans
 * enregistrer / Enregistrer et continuer. À rendre : `dialog`.
 */
export function useSwitchGuard(): { guard: (prefixes: string[], go: () => void) => void; dialog: ReactNode } {
  const { t } = useTranslation()
  const registry = useContext(UnsavedContext)
  const [pending, setPending] = useState<{ ids: [string, UnsavedState][]; go: () => void } | null>(null)
  const [saving, setSaving] = useState(false)
  const guard = useCallback(
    (prefixes: string[], go: () => void) => {
      // Lu sur le registre vivant : un onglet qui vient d'enregistrer ne retient pas.
      const ids = Object.entries(registry?.live.current ?? {}).filter(
        ([id, s]) => s.dirty && prefixes.some((p) => id.startsWith(p)),
      )
      if (ids.length === 0) go()
      else setPending({ ids, go })
    },
    [registry],
  )
  if (!pending || !registry) return { guard, dialog: null }
  const { ids, go } = pending
  const discardAndGo = () => {
    for (const [id] of ids) registry.discards.current[id]?.()
    setPending(null)
    go()
  }
  const saveAndGo = async () => {
    setSaving(true)
    try {
      for (const [id] of ids) await registry.saves.current[id]?.()
      setPending(null)
      go()
    } catch {
      // L'onglet montre son erreur ; on reste pour la corriger.
      setPending(null)
    } finally {
      setSaving(false)
    }
  }
  const dialog = (
    <UnsavedDialog
      text={t('unsavedSwitchText')}
      entries={ids}
      discardText={t('unsavedSwitchDiscard')}
      saveText={t('unsavedSwitchSave')}
      saving={saving}
      onStay={() => setPending(null)}
      onDiscard={discardAndGo}
      onSave={() => void saveAndGo()}
    />
  )
  return { guard, dialog }
}

function UnsavedDialog({
  text,
  entries,
  discardText,
  saveText,
  saving,
  onStay,
  onDiscard,
  onSave,
}: {
  text: string
  entries: [string, UnsavedState][]
  discardText: string
  saveText: string
  saving: boolean
  onStay: () => void
  onDiscard: () => void
  onSave: () => void
}) {
  const { t } = useTranslation()
  const blocking = entries.some(([, s]) => !s.saveable)
  return (
    <div role="dialog" aria-label={t('unsavedTitle')} className="dialog-backdrop">
      <div className="panel dialog-panel stack">
        <h2>{t('unsavedTitle')}</h2>
        <p>{text}</p>
        <ul>
          {entries.map(([id, s]) => (
            <li key={id}>
              {s.label}
              {!s.saveable ? <span className="muted"> — {t('unsavedNotSaveable')}</span> : null}
            </li>
          ))}
        </ul>
        <div className="row">
          <button type="button" className="btn" onClick={onStay}>
            {t('unsavedStay')}
          </button>
          <button type="button" className="btn" onClick={onDiscard}>
            {discardText}
          </button>
          {!blocking ? (
            <BusyButton type="button" busy={saving} className="btn btn-primary" busyText={t('unsavedSaving')} onClick={onSave}>
              {saveText}
            </BusyButton>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function BeforeUnload() {
  const dirty = useDirty().length > 0
  useEffect(() => {
    if (!dirty) return
    // Fermeture ou rechargement de l'onglet du navigateur : seule l'alerte
    // native est possible (on ne peut pas enregistrer depuis elle).
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])
  return null
}

function LeaveGuard() {
  const { t } = useTranslation()
  const registry = useContext(UnsavedContext)!
  const dirtyIds = Object.entries(registry.entries).filter(([, s]) => s.dirty)
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      Object.values(registry.live.current).some((s) => s.dirty) &&
      currentLocation.pathname !== nextLocation.pathname,
  )
  const [saving, setSaving] = useState(false)
  if (blocker.state !== 'blocked') return <BeforeUnload />
  const saveAndLeave = async () => {
    setSaving(true)
    try {
      for (const [id] of dirtyIds) await registry.saves.current[id]?.()
      blocker.proceed()
    } catch {
      // L'onglet montre son erreur ; on reste pour la corriger.
      blocker.reset()
    } finally {
      setSaving(false)
    }
  }
  return (
    <UnsavedDialog
      text={t('unsavedText')}
      entries={dirtyIds}
      discardText={t('unsavedDiscard')}
      saveText={t('unsavedSave')}
      saving={saving}
      onStay={() => blocker.reset()}
      onDiscard={() => blocker.proceed()}
      onSave={() => void saveAndLeave()}
    />
  )
}
