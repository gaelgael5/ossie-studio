import { configureMonacoYaml } from 'monaco-yaml'

import { monaco } from '../monaco/setup'
import draft07 from './json-schema-draft-07.json'

/**
 * L'aide YAML de l'onglet Modèle d'un référentiel (repris de Miles,
 * `portail/frontend/src/dataModels/monaco.ts`), par-dessus le réglage commun de
 * Monaco (`../monaco/setup`). Ce module n'est importé que par l'onglet Modèle,
 * chargé à la demande : le reste de l'application ne le paie pas.
 */
export const MODEL_URI_SUFFIX = '.schema.yaml'

// Aide à la saisie seulement (complétion, survol) : monaco-yaml lit mal les méta-schémas
// en plusieurs documents (2020-12 en compte huit). L'arbitre reste le serveur.
configureMonacoYaml(monaco, {
  enableSchemaRequest: false,
  schemas: [{ fileMatch: [`**/*${MODEL_URI_SUFFIX}`], uri: 'ossie:/meta/json-schema', schema: draft07 }],
})
