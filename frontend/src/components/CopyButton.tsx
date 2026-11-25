import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

// Bouton « copier » avec un retour visible : le libellé passe à « ✓ copié »
// (petite animation) pendant un instant, ou dit que la copie a échoué.

/** Durée du retour « copié » avant de revenir au libellé normal. */
export const COPY_FEEDBACK_MS = 1600

/**
 * Copie un texte dans le presse-papier. L'API moderne n'existe que sur une page
 * sûre (HTTPS ou localhost) : sur une instance servie en HTTP, on retombe sur
 * l'ancienne méthode (sélection d'un champ caché + commande « copier »).
 * Rend true si la copie a réussi.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // permission refusée : on tente le repli
    }
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  let copied = false
  try {
    copied = document.execCommand('copy')
  } catch {
    copied = false
  }
  area.remove()
  return copied
}

type CopyState = 'idle' | 'copied' | 'failed'

export function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation()
  const [state, setState] = useState<CopyState>('idle')

  useEffect(() => {
    if (state === 'idle') return
    const timer = window.setTimeout(() => setState('idle'), COPY_FEEDBACK_MS)
    return () => window.clearTimeout(timer)
  }, [state])

  const label = state === 'copied' ? `✓ ${t('copied')}` : state === 'failed' ? t('copyFailed') : t('copy')
  return (
    <button
      type="button"
      className={`btn-link copy-button${state === 'copied' ? ' is-copied' : ''}${state === 'failed' ? ' is-failed' : ''}`}
      onClick={async () => setState((await copyText(text)) ? 'copied' : 'failed')}
    >
      {/* Annoncé aux lecteurs d'écran quand le libellé change. */}
      <span aria-live="polite">{label}</span>
    </button>
  )
}
