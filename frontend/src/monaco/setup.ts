import { loader } from '@monaco-editor/react'
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution.js'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api.js'

/**
 * Le seul réglage de Monaco de l'application : la visionneuse de code (raw) et les
 * éditeurs des référentiels l'importent tous deux. `MonacoEnvironment` et le
 * chargeur sont globaux : deux réglages séparés s'écraseraient selon la page ouverte
 * en premier (et l'aide YAML des référentiels perdrait son travailleur).
 *
 * Monaco embarqué dans le bundle, jamais tiré d'un CDN : l'instance doit marcher
 * sans Internet et ce qui est affiché ne sort pas vers un tiers. Seuls le cœur de
 * l'éditeur et la coloration YAML sont importés.
 *
 * Travailleurs construits ici, à l'appel : avec les classes `?worker` de Vite, l'ordre
 * d'évaluation des morceaux les laissait indéfinies quand Monaco les demandait (Miles,
 * constaté dans Chromium le 23/10/2026). Le travailleur `yaml` n'est demandé que là où
 * monaco-yaml est configuré (onglet Modèle d'un référentiel).
 *
 * Monaco tenu en 0.52.2, exprès : à partir de 0.53, `editor.createWebWorker` attend un
 * `worker` tout fait, alors que monaco-yaml 5.5 lui passe encore `moduleId` et `label`
 * — aucun travailleur YAML (constaté dans Miles).
 */
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

/**
 * Le thème de **tous** les éditeurs Monaco de l'application (ticket 46cf70a6) :
 * sombre, aux couleurs de la maquette, déclaré ici seulement. Un éditeur le prend
 * par `theme={MONACO_THEME}` et ne passe jamais ses propres couleurs (un réglage
 * éditeur par éditeur finirait par donner des écrans qui ne se ressemblent plus).
 * Les jetons non listés suivent le thème sombre standard de Monaco.
 */
export const MONACO_THEME = 'ossie-dark'

export const MONACO_THEME_COLORS = {
  background: '#1e1e1e',
  lineNumber: '#6e7681',
  key: '#9cdcfe',
  string: '#ce9178',
  number: '#b5cea8',
  keyword: '#569cd6',
  comment: '#6a9955',
}

const hex = (color: string) => color.slice(1)
const c = MONACO_THEME_COLORS
monaco.editor.defineTheme(MONACO_THEME, {
  base: 'vs-dark',
  inherit: true,
  rules: [
    // Jetons de la coloration YAML de Monaco : clé = `type`, booléens et `null` = `keyword` ;
    // `number` couvre aussi `number.float`, `number.date`…
    { token: 'type', foreground: hex(c.key) },
    { token: 'string', foreground: hex(c.string) },
    { token: 'number', foreground: hex(c.number) },
    { token: 'keyword', foreground: hex(c.keyword) },
    { token: 'comment', foreground: hex(c.comment), fontStyle: 'italic' },
  ],
  colors: {
    'editor.background': c.background,
    'editorGutter.background': c.background,
    'editorLineNumber.foreground': c.lineNumber,
    'editorLineNumber.activeForeground': '#c6c6c6',
  },
})

export { monaco }
