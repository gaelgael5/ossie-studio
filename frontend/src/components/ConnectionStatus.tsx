import { useTranslation } from 'react-i18next'

// État affiché d'une connexion (forge ou base). « Jamais testé » est un état à
// part entière : le secret a changé depuis le dernier test, ni valide ni en
// erreur (secretstore/usages.py, NEVER_TESTED).
export function ConnectionStatus({ status, errorCode }: { status: string; errorCode: string | null }) {
  const { t } = useTranslation()
  if (status === 'valid') return <span className="text-ok">{t('statusValid')}</span>
  if (status === 'never_tested') return <span className="text-warn">{t('statusNeverTested')}</span>
  return (
    <span className="text-danger">{t(`errors.${errorCode}`, { defaultValue: t('statusError') })}</span>
  )
}
