import { useTranslation } from 'react-i18next'

// Une erreur de collecte, dite en clair : ce qui s'est passé (cause traduite),
// la précision technique du parseur (ligne, octet, clé fautive…) et ce qu'il
// faut faire. Le backend ne rédige jamais le message : il donne un code stable
// et un détail ; une cause inconnue de l'interface s'affiche avec son code.

/** Clé i18n d'une cause : `:` est le séparateur d'espace de noms d'i18next. */
function causeKey(cause: string): string {
  return cause.replace(/:/g, '_')
}

/** Message d'une ligne de scan_files : le code, puis le détail à la ligne. */
export function splitScanMessage(message: string): { cause: string; detail: string } {
  const at = message.indexOf('\n')
  return at < 0
    ? { cause: message, detail: '' }
    : { cause: message.slice(0, at), detail: message.slice(at + 1) }
}

export function CollectErrorCause({ cause, detail }: { cause: string; detail?: string }) {
  const { t } = useTranslation()
  const key = causeKey(cause)
  const label = t(`collectCause.${key}`, { defaultValue: t('collectCauseUnknown', { cause }) })
  const help = t(`collectCauseHelp.${key}`, { defaultValue: '' })
  return (
    <div className="collect-cause">
      <div>{label}</div>
      {detail ? <div className="mono small collect-cause-detail">{detail}</div> : null}
      {help ? <div className="muted small">{help}</div> : null}
    </div>
  )
}
