import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ErrorList } from '../components/ErrorList'
import { ARCHIMATE_TAG, loadUrbanArchimate } from './urbanArchimate'
import type { ArchimateDelegates, ArchimateElement } from './urbanArchimate'

// Le diagramme d'un modèle (ticket f5421cc7) : l'élément `<urban-archimate>`, qui reçoit
// le modèle hydraté par Ossie et ses délégués (boîte à outils, pose, relations
// permises). Son thème suit celui d'Ossie par le raccord `urban-archimate { --ua-* }`
// de styles.css. En lecture seule, ni boîte à outils ni modification.

interface Props {
  hydrated: Record<string, unknown>
  delegates: ArchimateDelegates
  readOnly: boolean
  /** À chaque modification du dessin (indicateur « modifié »). */
  onChanged?: () => void
  /** Une fois le modèle chargé : l'élément, pour l'enregistrement (ticket c3dd045c). */
  onReady?: (element: ArchimateElement) => void
}

export function ModelDiagram({ hydrated, delegates, readOnly, onChanged, onReady }: Props) {
  const { t } = useTranslation()
  const host = useRef<HTMLDivElement | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Les rappels changent à chaque rendu : on garde les derniers sans recréer l'élément.
  const latest = useRef({ delegates, onChanged, onReady })
  latest.current = { delegates, onChanged, onReady }

  useEffect(() => {
    let cancelled = false
    const container = host.current
    setError(null)
    loadUrbanArchimate()
      .then(async () => {
        if (cancelled || !container) return
        const element = document.createElement(ARCHIMATE_TAG) as ArchimateElement
        // Avant l'ajout au document : le mode est lu à la création de l'éditeur.
        element.readOnly = readOnly
        element.delegates = {
          toolbox: (context) => latest.current.delegates.toolbox?.(context) ?? Promise.resolve([]),
          drop: (cle) => latest.current.delegates.drop!(cle),
          allowedRelations: (source, target) =>
            latest.current.delegates.allowedRelations?.(source, target) ?? Promise.resolve([]),
        }
        element.addEventListener('changed', () => latest.current.onChanged?.())
        container.replaceChildren(element)
        await element.loadModel(hydrated)
        if (!cancelled) latest.current.onReady?.(element)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
      container?.replaceChildren()
    }
  }, [hydrated, readOnly])

  return (
    <>
      <ErrorList messages={error ? [t('cartoDiagramError', { message: error })] : []} />
      <div ref={host} className="carto-model-canvas" aria-label={t('cartoDiagram')} role="region" />
    </>
  )
}
