import { type JsonSchema, SchemaFieldsEditor, en, fr } from 'jsonjoy-builder'
import 'jsonjoy-builder/styles.css'
import { lazy, Suspense } from 'react'
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
 */
export default function SchemaEditor({
  path,
  text,
  parsed,
  readOnly,
  onVisual,
  onYaml,
}: {
  path: string
  text: string
  parsed: Parsed
  readOnly: boolean
  onVisual: (schema: Record<string, unknown>) => void
  onYaml: (text: string) => void
}) {
  const { t, i18n } = useTranslation()
  return (
    <div className="model-editor">
      <section aria-label={t('refModelVisual')}>
        <h2>{t('refModelVisual')}</h2>
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
      </section>
      <section aria-label={t('refModelYaml')}>
        <h2>{t('refModelYaml')}</h2>
        <Suspense fallback={<p className="muted">{t('loading')}</p>}>
          <YamlEditor path={path} value={text} onChange={onYaml} readOnly={readOnly} />
        </Suspense>
      </section>
    </div>
  )
}
