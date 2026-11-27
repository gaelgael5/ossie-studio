import { useTranslation } from 'react-i18next'
import { sessionCapabilities } from '../apiClient'
import { useDraftView } from '../draftView'
import { useSession } from '../hooks'

// Bug a512d8c3 : les vues « Publiée » et « Brouillon » d'une branche sont des
// vues de lecture (ticket 22c71979). Toute écriture va dans le brouillon, que la
// vue « Publiée » ne montre pas : un ajout s'y heurtait à un objet invisible.
// On n'écrit donc qu'en vue « Fusionnée » (la branche avec le brouillon).

/** Peut-on modifier la cartographie ici : le droit, et la vue fusionnée. */
export function useCartoWrite(branchId: string): { canWrite: boolean; blockedByView: boolean } {
  const session = useSession()
  const view = useDraftView(branchId)
  const allowed = sessionCapabilities(session.data).includes('cartographie:write')
  return { canWrite: allowed && view === 'merged', blockedByView: allowed && view !== 'merged' }
}

/** La note qui dit pourquoi l'écran est en lecture seule, et comment modifier. */
export function CartoViewNote({ branchId }: { branchId: string }) {
  const { t } = useTranslation()
  const { blockedByView } = useCartoWrite(branchId)
  return blockedByView ? (
    <p className="banner" role="note">
      {t('cartoViewReadOnly')}
    </p>
  ) : null
}
