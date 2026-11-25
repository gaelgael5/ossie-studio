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
  set: (id: string, state: UnsavedState | null) => void
  saves: React.MutableRefObject<Record<string, () => Promise<unknown>>>
}

const UnsavedContext = createContext<Registry | null>(null)

export function UnsavedProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<Record<string, UnsavedState>>({})
  const saves = useRef<Record<string, () => Promise<unknown>>>({})
  const set = useCallback((id: string, state: UnsavedState | null) => {
    setEntries((prev) => {
      if (state === null) {
        if (!(id in prev)) return prev
        const next = { ...prev }
        delete next[id]
        return next
      }
      const old = prev[id]
      if (old && old.dirty === state.dirty && old.saveable === state.saveable && old.label === state.label)
        return prev
      return { ...prev, [id]: state }
    })
  }, [])
  const value = useMemo(() => ({ entries, set, saves }), [entries, set])
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

/** Déclare l'état d'un onglet ; `save` est relu au moment d'enregistrer. */
export function useUnsaved(id: string, state: UnsavedState, save: () => Promise<unknown>) {
  const registry = useContext(UnsavedContext)
  const set = registry?.set
  const saves = registry?.saves
  useEffect(() => {
    if (saves) saves.current[id] = save
  })
  useEffect(() => {
    set?.(id, state)
  }, [set, id, state.label, state.dirty, state.saveable]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(
    () => () => {
      set?.(id, null)
      if (saves) delete saves.current[id]
    },
    [set, saves, id],
  )
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
      dirtyIds.length > 0 && currentLocation.pathname !== nextLocation.pathname,
  )
  const [saving, setSaving] = useState(false)
  if (blocker.state !== 'blocked') return <BeforeUnload />
  const blocking = dirtyIds.filter(([, s]) => !s.saveable)
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
    <div role="dialog" aria-label={t('unsavedTitle')} className="dialog-backdrop">
      <div className="panel dialog-panel stack">
        <h2>{t('unsavedTitle')}</h2>
        <p>{t('unsavedText')}</p>
        <ul>
          {dirtyIds.map(([id, s]) => (
            <li key={id}>
              {s.label}
              {!s.saveable ? <span className="muted"> — {t('unsavedNotSaveable')}</span> : null}
            </li>
          ))}
        </ul>
        <div className="row">
          <button type="button" className="btn" onClick={() => blocker.reset()}>
            {t('unsavedStay')}
          </button>
          <button type="button" className="btn" onClick={() => blocker.proceed()}>
            {t('unsavedDiscard')}
          </button>
          {blocking.length === 0 ? (
            <BusyButton
              type="button"
              busy={saving}
              className="btn btn-primary"
              busyText={t('unsavedSaving')}
              onClick={() => void saveAndLeave()}
            >
              {t('unsavedSave')}
            </BusyButton>
          ) : null}
        </div>
      </div>
    </div>
  )
}
