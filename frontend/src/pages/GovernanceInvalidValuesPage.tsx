import { useTranslation } from 'react-i18next'
import { GovernanceFrame } from '../components/GovernanceFrame'

/**
 * Onglet « Valeurs invalides » : il recense les valeurs de classification
 * portées par des assets dont le niveau a été supprimé ou renommé. Ces valeurs
 * sont dérivées des assets, qui n'existent pas encore (epic Curation) : la page
 * affiche donc un état vide honnête, à sa place, prête à se remplir plus tard.
 */
export function GovernanceInvalidValuesPage() {
  const { t } = useTranslation()
  return (
    <GovernanceFrame active="invalid" subtitle={t('invalidValuesIntro')}>
      <div className="empty-note">{t('invalidValuesPending')}</div>
    </GovernanceFrame>
  )
}
