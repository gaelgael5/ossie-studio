import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

// Message « aucune branche » avec le chemin pour en sortir : un lien vers
// l'écran de l'espace où l'on choisit (ou crée) la branche de base.
export function NoBranchNote({ spaceId }: { spaceId: string | null }) {
  const { t } = useTranslation()
  return (
    <div className="empty-note">
      {t('noBranchYet')}{' '}
      {spaceId ? <Link to={`/spaces/${spaceId}`}>{t('chooseBaseBranchLink')}</Link> : null}
    </div>
  )
}
