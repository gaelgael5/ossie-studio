import { useTranslation } from 'react-i18next'
import type { ImportTrial } from '../apiClient'

// Compte rendu d'un essai ou d'un import (tickets cda9c43a, a909dc66) : compteurs,
// erreurs rattachées à leur ligne du fichier, différences avec les valeurs actuelles.

export function shownCell(value: unknown): string {
  if (value === undefined || value === null) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function shownDate(iso: string, locale: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' })
}

export function ImportReport({ report }: { report: ImportTrial }) {
  const { t } = useTranslation()
  if (report.template_problems.length > 0) {
    return (
      <div className="stack" role="status">
        {report.template_problems.map((p, i) => (
          <p key={i} className="text-danger">
            {p.line ? t('refImportTemplateLine', { line: p.line }) : ''} {p.message}
          </p>
        ))}
      </div>
    )
  }
  return (
    <div className="stack" role="status">
      <div className="ref-counters">
        <span>{t('refImportLines', { count: report.lines ?? 0 })}</span>
        <span className="badge">{t('refImportNew', { count: report.new ?? 0 })}</span>
        <span className="badge badge-warn">{t('refImportModified', { count: report.modified ?? 0 })}</span>
        <span className="badge badge-muted">{t('refImportUnchanged', { count: report.unchanged ?? 0 })}</span>
        {report.deleted ? (
          <span className="badge badge-danger">{t('refImportDeleted', { count: report.deleted })}</span>
        ) : null}
        <span className="badge badge-danger">{t('refImportErrors', { count: report.errors ?? 0 })}</span>
      </div>
      {(report.error_details ?? []).length > 0 ? (
        <ul className="plain-list" aria-label={t('refImportErrorList')}>
          {(report.error_details ?? []).map((e, i) => (
            <li key={i} className="text-danger small">
              <strong>{e.where}</strong> — {t(`refValueError_${e.code}`, { defaultValue: e.code })}
              {e.field ? ` (${e.field})` : ''}
              {e.path ? ` ${e.path}` : ''}
              {e.expected ? ` → ${e.expected}` : ''}
              {e.message && e.code !== 'key_duplicate' ? ` : ${e.message}` : ''}
            </li>
          ))}
        </ul>
      ) : null}
      {(report.differences ?? []).length > 0 ? (
        <ul className="plain-list small" aria-label={t('refImportDifferences')}>
          {(report.differences ?? []).map((d, i) => (
            <li key={i}>
              <strong>{d.key}</strong> {d.where ? `${t('refImportDiffWhere', { where: d.where })} ` : '— '}
              {d.kind === 'new'
                ? t('refImportDiffNew')
                : d.kind === 'deleted'
                  ? t('refImportDiffDeleted')
                  : (d.changes ?? [])
                      .map((c) => `${c.property} : ${shownCell(c.before)} → ${shownCell(c.after)}`)
                      .join(' · ')}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
