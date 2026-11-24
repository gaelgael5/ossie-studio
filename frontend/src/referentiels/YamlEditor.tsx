import Editor from '@monaco-editor/react'
import { useTranslation } from 'react-i18next'

import { MONACO_THEME } from '../monaco/setup'
import { MODEL_URI_SUFFIX } from './monaco'

/** Monaco pour le YAML du modèle, complété et vérifié à la frappe par monaco-yaml
 * (repris de Miles). */
export default function YamlEditor({
  path,
  value,
  onChange,
  readOnly,
}: {
  path: string
  value: string
  onChange: (value: string) => void
  readOnly: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="yaml-editor" aria-label={t('refModelYaml')} role="group">
      <Editor
        height="560px"
        language="yaml"
        theme={MONACO_THEME}
        // Le motif du fichier rattache le méta-schéma (voir monaco.ts).
        path={path.endsWith(MODEL_URI_SUFFIX) ? path : `${path}${MODEL_URI_SUFFIX}`}
        value={value}
        onChange={(next) => onChange(next ?? '')}
        options={{ readOnly, minimap: { enabled: false }, tabSize: 2, scrollBeyondLastLine: false }}
      />
    </div>
  )
}
