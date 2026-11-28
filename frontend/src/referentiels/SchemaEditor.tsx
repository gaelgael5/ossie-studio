import { type JsonSchema, SchemaFieldsEditor, en, fr } from 'jsonjoy-builder'
import 'jsonjoy-builder/styles.css'
import { lazy, Suspense, type ReactNode } from 'react'
import { SplitPane } from '../components/SplitPane'
import { useTranslation } from 'react-i18next'

import type { Parsed } from './modelRules'

// Monaco ne se charge qu'à l'ouverture de l'onglet Modèle (comme dans Miles).
const YamlEditor = lazy(async () => {
  await import('./monaco')
  return import('./YamlEditor')
})

/**
 * L'éditeur de modèle repris de Miles (`portail/frontend/src/dataModels/SchemaEditor.tsx`) :
 * l'éditeur visuel JSONJoy à gauche, le YAML dans Monaco à droite, sur le même modèle en
 * mémoire. Le parent tient le texte et sa lecture ; rien n'est écrit ici.
 *
 * Repris tel quel par la fenêtre d'édition d'un type de cartographie (ticket
 * 5c39d4df) : `before` s'affiche au-dessus de l'éditeur visuel (propriétés de base
 * en lecture seule), `yamlActions` à côté du titre du YAML.
 */
export default function SchemaEditor({
  path,
  text,
  parsed,
  readOnly,
  onVisual,
  onYaml,
  visualTitle,
  before,
  after,
  yamlActions,
  splitId,
}: {
  path: string
  text: string
  parsed: Parsed
  readOnly: boolean
  onVisual: (schema: Record<string, unknown>) => void
  onYaml: (text: string) => void
  visualTitle?: string
  before?: ReactNode
  /** Sous l'éditeur visuel (propriétés de base d'un type, ticket e2b9d7de). */
  after?: ReactNode
  yamlActions?: ReactNode
  /** Barre de séparation entre l'éditeur visuel et le YAML (ticket f74c8e74) :
   * l'identifiant de l'écran, sous lequel la largeur est retenue. */
  splitId?: string
}) {
  const { t, i18n } = useTranslation()
  const visual = (
      <section aria-label={visualTitle ?? t('refModelVisual')} className={after ? 'model-editor-with-after' : undefined}>
        <h2>{visualTitle ?? t('refModelVisual')}</h2>
        {before}
        {parsed.ok ? (
          <SchemaFieldsEditor
            value={parsed.model as JsonSchema}
            onChange={(schema) => onVisual(schema as Record<string, unknown>)}
            readOnly={readOnly}
            locale={i18n.language.startsWith('fr') ? fr : en}
          />
        ) : (
          <p className="notice" role="note">
            {t('refModelUnreadable', { message: parsed.message })}
          </p>
        )}
        {after}
      </section>
  )
  const yaml = (
      <section aria-label={t('refModelYaml')}>
        <div className="row model-editor-head">
          <h2>{t('refModelYaml')}</h2>
          {yamlActions}
        </div>
        <Suspense fallback={<p className="muted">{t('loading')}</p>}>
          <YamlEditor path={path} value={text} onChange={onYaml} readOnly={readOnly} />
        </Suspense>
      </section>
  )
  if (splitId) return <SplitPane id={splitId} left={visual} right={yaml} />
  return (
    <div className="model-editor">
      {visual}
      {yaml}
    </div>
  )
}
