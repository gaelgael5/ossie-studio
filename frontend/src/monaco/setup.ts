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

export { monaco }
