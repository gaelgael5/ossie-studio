import { loader } from '@monaco-editor/react'
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution.js'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js'
import { configureMonacoYaml } from 'monaco-yaml'

import draft07 from './json-schema-draft-07.json'

/**
 * Monaco servi par l'application elle-même, jamais depuis un CDN (repris de Miles,
 * `portail/frontend/src/dataModels/monaco.ts`). Ce module n'est importé que par
 * l'onglet Modèle d'un référentiel, chargé à la demande : le reste de l'application
 * ne le paie pas.
 */
// Travailleurs construits ici, à l'appel : avec les classes `?worker` de Vite, l'ordre
// d'évaluation des morceaux les laissait indéfinies quand Monaco les demandait (Miles,
// constaté dans Chromium le 23/10/2026).
self.MonacoEnvironment = {
  getWorker(_moduleId: string, label: string) {
    if (label === 'yaml') {
      return new Worker(new URL('./yaml.worker.ts', import.meta.url), { type: 'module' })
    }
    return new Worker(new URL('monaco-editor/esm/vs/editor/editor.worker.js', import.meta.url), {
      type: 'module',
    })
  },
}

loader.config({ monaco })
// Monaco tenu en 0.52 (package.json « ~0.52.2 »), exprès : à partir de 0.53,
// `editor.createWebWorker` attend un `worker` tout fait, alors que monaco-yaml 5.5 lui
// passe encore `moduleId` et `label` — aucun travailleur YAML (constaté dans Miles).

export const MODEL_URI_SUFFIX = '.schema.yaml'

// Aide à la saisie seulement (complétion, survol) : monaco-yaml lit mal les méta-schémas
// en plusieurs documents (2020-12 en compte huit). L'arbitre reste le serveur.
configureMonacoYaml(monaco, {
  enableSchemaRequest: false,
  schemas: [{ fileMatch: [`**/*${MODEL_URI_SUFFIX}`], uri: 'ossie:/meta/json-schema', schema: draft07 }],
})
