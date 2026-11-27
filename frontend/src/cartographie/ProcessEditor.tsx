import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { CartoBpmnObject } from '../apiClient'
import { ErrorList } from '../components/ErrorList'
import { BPMN_TAG, loadUrbanBpmn } from './urbanBpmn'
import type { BpmnDelegates, BpmnElement } from './urbanBpmn'

// L'éditeur d'un processus (ticket 48335b93) : l'élément `<urban-bpmn>`, qui reçoit le
// XML tel quel et les objets référencés avec leur nom à jour (il les applique), et les
// délégués d'Ossie (boîte à outils, pose, action d'un avertissement, rattacher un
// élément). Son thème suit celui d'Ossie par le raccord `urban-bpmn { --ub-* }` de
// styles.css. En lecture seule : on explore, on ne modifie pas.

interface Props {
  xml: string
  references: CartoBpmnObject[]
  delegates: BpmnDelegates
  readOnly: boolean
  /** À chaque modification du dessin (indicateur « modifié »). */
  onChanged?: () => void
  /** Une fois le processus chargé : l'élément, pour l'enregistrement. */
  onReady?: (element: BpmnElement) => void
}

export function ProcessEditor({ xml, references, delegates, readOnly, onChanged, onReady }: Props) {
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
    loadUrbanBpmn()
      .then(async () => {
        if (cancelled || !container) return
        const element = document.createElement(BPMN_TAG) as BpmnElement
        // Avant l'ajout au document : le mode est lu à la création de l'éditeur.
        element.readOnly = readOnly
        element.delegates = {
          toolbox: (context) => latest.current.delegates.toolbox?.(context) ?? Promise.resolve([]),
          drop: (cle) => latest.current.delegates.drop!(cle),
          action: (cle, action) => latest.current.delegates.action?.(cle, action),
          attach: (context) => latest.current.delegates.attach?.(context) ?? Promise.resolve(null),
        }
        element.addEventListener('changed', () => latest.current.onChanged?.())
        container.replaceChildren(element)
        await element.loadXml(xml, { references })
        if (!cancelled) latest.current.onReady?.(element)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
      container?.replaceChildren()
    }
  }, [xml, references, readOnly])

  return (
    <>
      <ErrorList messages={error ? [t('cartoProcessEditorError', { message: error })] : []} />
      <div ref={host} className="carto-process-editor" aria-label={t('cartoProcessEditor')} role="region" />
    </>
  )
}
