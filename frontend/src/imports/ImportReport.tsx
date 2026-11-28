import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import type { ImportTag, ImportTaggedLine, ImportTrial, ImportTrialDifference, ImportUsage } from '../apiClient'
import { explainProblem } from './templateHint'

// Compte rendu d'un essai ou d'un import (tickets cda9c43a, a909dc66) : compteurs,
// erreurs rattachées à leur ligne du fichier, différences avec les valeurs actuelles,
// et — selon la stratégie de suppression (ticket 6d36073b) — les éléments supprimés
// et les absents gardés, avec la raison (et les utilisations qui retiennent un élément).

/** Un nom qui mène à sa fiche quand la cible en a une. */
export function ItemLink({ to, children }: { to: string | null | undefined; children: ReactNode }) {
  return to ? <Link to={to}>{children}</Link> : <>{children}</>
}

/** Une utilisation : la relation (son type), et l'objet à son autre bout. */
export function UsageLine({ usage, itemPath }: { usage: ImportUsage; itemPath?: (ref: string) => string }) {
  const { t } = useTranslation()
  const other = usage.other
  return (
    <>
      {usage.type_title} <ItemLink to={itemPath?.(usage.ref)}>{usage.name || usage.key}</ItemLink>{' '}
      {t(usage.role === 'target' ? 'refImportUsageFrom' : 'refImportUsageTo')}{' '}
      <ItemLink to={other.ref ? itemPath?.(other.ref) : null}>{other.name ?? other.key ?? other.ref}</ItemLink>
      {other.type_title ? ` (${other.type_title})` : ''}
    </>
  )
}

function Absent({ d, itemPath }: { d: ImportTrialDifference; itemPath?: (ref: string) => string }) {
  const { t } = useTranslation()
  if (d.kind === 'deleted') return <>{t('refImportDiffDeleted')}</>
  return (
    <>
      {t('refImportDiffKept', { reason: t(`refImportKeptReason_${d.reason ?? 'strategy_keep'}`) })}
      {(d.usages ?? []).length > 0 ? (
        <ul className="plain-list">
          {(d.usages ?? []).map((u) => (
            <li key={u.ref}>
              <UsageLine usage={u} itemPath={itemPath} />
            </li>
          ))}
        </ul>
      ) : null}
    </>
  )
}

/** Un tag d'une ligne (ticket 43478978) : « pays : XX absente du référentiel Pays ». */
export function TagText({ tag }: { tag: ImportTag }) {
  const { t } = useTranslation()
  return <>{t('refImportTag', { property: tag.properties.join(' + '), value: tag.value, referentiel: tag.title })}</>
}

/** Les lignes taguées d'un compte rendu ; `refuse` : les cases « Refuser » (revue
 * avant l'import), sinon la liste dit lesquelles ont été refusées. */
export function TaggedLines({
  lines,
  refuse,
}: {
  lines: ImportTaggedLine[]
  refuse?: { refused: Set<string>; onToggle: (where: string) => void }
}) {
  const { t } = useTranslation()
  if (lines.length === 0) return null
  return (
    <ul className="plain-list small ref-import-tagged" aria-label={t('refImportTaggedList')}>
      {lines.map((line) => (
        <li key={line.where} className="row">
          {refuse ? (
            <label className="row">
              <input
                type="checkbox"
                checked={refuse.refused.has(line.where)}
                onChange={() => refuse.onToggle(line.where)}
                aria-label={t('refImportRefuseLine', { where: line.where, key: line.key })}
              />
              {t('refImportRefuse')}
            </label>
          ) : line.refused ? (
            <span className="badge badge-danger">{t('refImportRefusedBadge')}</span>
          ) : null}
          <span>
            <strong>{line.key}</strong> {t('refImportDiffWhere', { where: line.where })}{' '}
            <span className="muted">({t(`refImportTaggedKind_${line.kind}`)})</span> —{' '}
            {line.tags.map((tag, i) => (
              <span key={i}>
                {i > 0 ? ' · ' : ''}
                <TagText tag={tag} />
              </span>
            ))}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function shownCell(value: unknown): string {
  if (value === undefined || value === null) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function shownDate(iso: string, locale: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' })
}

export function ImportReport({
  report,
  source,
  itemPath,
}: {
  report: ImportTrial
  source?: string
  /** La fiche d'un élément (cartographie), pour les liens des absents gardés. */
  itemPath?: (ref: string) => string
}) {
  const { t } = useTranslation()
  if (report.template_problems.length > 0) {
    return (
      <div className="stack" role="status">
        {report.template_problems.map((p, i) => {
          // Le template essayé (`source`) donne l'extrait de la ligne fautive et
          // permet d'expliquer le message du moteur (ticket 79e85292).
          const explained = source === undefined ? null : explainProblem(p, source)
          return (
            <div key={i} className="stack template-problem">
              <p className="text-danger">
                {p.line ? t('refImportTemplateLine', { line: p.line }) : ''} {p.message}
              </p>
              {explained?.excerpt ? (
                <pre className="template-excerpt" aria-label={t('refImportTemplateExcerpt')}>
                  {explained.excerpt.text}
                  {'\n'}
                  <span className="text-danger">{explained.excerpt.caret}</span>
                </pre>
              ) : null}
              {explained?.reason ? <p>{t(`refImportTemplateReason_${explained.reason.key}`, { token: explained.reason.token })}</p> : null}
              {explained?.hints.map((h) => (
                <p key={h} className="muted">
                  {t(`refImportTemplateHint_${h}`)}
                </p>
              ))}
            </div>
          )
        })}
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
        {report.kept ? <span className="badge badge-muted">{t('refImportKept', { count: report.kept })}</span> : null}
        {report.kept_in_use ? (
          <span className="badge badge-warn">{t('refImportKeptInUseBadge', { count: report.kept_in_use })}</span>
        ) : null}
        <span className="badge badge-danger">{t('refImportErrors', { count: report.errors ?? 0 })}</span>
        {report.tagged ? <span className="badge badge-warn">{t('refImportTagged', { count: report.tagged })}</span> : null}
        {report.refused ? <span className="badge badge-danger">{t('refImportRefusedCount', { count: report.refused })}</span> : null}
      </div>
      <TaggedLines lines={report.tagged_lines ?? []} />
      {(report.error_details ?? []).length > 0 ? (
        <ul className="plain-list" aria-label={t('refImportErrorList')}>
          {(report.error_details ?? []).map((e, i) => {
            // Une erreur du template a son libellé ; un refus du document porte le
            // message de l'écriture commune, le même que par MCP (ticket 1612cce4).
            const label = t(`refValueError_${e.code}`, { defaultValue: '' })
            return (
              <li key={i} className="text-danger small">
                <strong>{e.where}</strong> —{' '}
                {label ? (
                  <>
                    {label}
                    {e.field ? ` (${e.field})` : ''}
                    {e.path ? ` ${e.path}` : ''}
                    {e.expected ? ` → ${e.expected}` : ''}
                    {e.message && e.code !== 'key_duplicate' ? ` : ${e.message}` : ''}
                  </>
                ) : (
                  e.message || e.code
                )}
              </li>
            )
          })}
        </ul>
      ) : null}
      {(report.differences ?? []).length > 0 ? (
        <ul className="plain-list small" aria-label={t('refImportDifferences')}>
          {(report.differences ?? []).map((d, i) => (
            <li key={i}>
              <strong>
                <ItemLink to={d.ref ? itemPath?.(d.ref) : null}>{d.key}</ItemLink>
              </strong>{' '}
              {d.where ? `${t('refImportDiffWhere', { where: d.where })} ` : '— '}
              {d.kind === 'new' ? (
                t('refImportDiffNew')
              ) : d.kind === 'deleted' || d.kind === 'kept' ? (
                <Absent d={d} itemPath={itemPath} />
              ) : (
                (d.changes ?? []).map((c) => `${c.property} : ${shownCell(c.before)} → ${shownCell(c.after)}`).join(' · ')
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
