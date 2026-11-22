import { useTranslation } from 'react-i18next'
import type { CollectProgressInfo } from '../apiClient'

// Checklist de la collecte en cours : étape courante (anneau), étapes franchies
// (✓), à venir (○). Le scan affiche un compteur (fichiers référencés dans la
// table de travail), le traitement une barre (fichiers traités / total), au
// prorata de chaque parseur — un parseur traite ses fichiers en un seul lot.
export function CollectChecklist({
  steps,
  progress,
  sourceType,
}: {
  steps: string[]
  progress: CollectProgressInfo
  sourceType: string
}) {
  const { t } = useTranslation()
  const percent = progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0
  const label = (step: string) =>
    step === 'download' && sourceType !== 'git' ? t('collectStep_download_db') : t(`collectStep_${step}`)
  return (
    <ol className="collect-steps" aria-label={t('collectProgressTitle')}>
      {steps.map((step) => {
        const done = progress.done.includes(step)
        const current = progress.step === step
        return (
          <li key={step} className={done ? 'is-done' : current ? 'is-current' : 'is-pending'}>
            <span className="collect-step-icon" aria-hidden="true">
              {done ? '✓' : current ? <span className="spinner spinner-sm" /> : '○'}
            </span>
            <span className="collect-step-body">
              <span>{label(step)}</span>
              {step === 'scan' && (current || done) ? (
                <span className="muted small">{t('collectScanned', { count: progress.scanned })}</span>
              ) : null}
              {step === 'process' && (current || done) ? (
                <span className="collect-bar-row">
                  <progress className="collect-bar" max={Math.max(progress.total, 1)} value={progress.processed}>
                    {percent}%
                  </progress>
                  <span className="muted small">
                    {t('collectProcessed', { done: progress.processed, total: progress.total, percent })}
                    {current && progress.current ? ` — ${progress.current}` : ''}
                  </span>
                </span>
              ) : null}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
