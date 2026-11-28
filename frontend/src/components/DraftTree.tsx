import { useState } from 'react'
import { useTranslation } from 'react-i18next'

// Fichiers du brouillon en arbre de répertoires (ticket 8810604c, Gael : « regrouper
// par répertoire et mettre la liste en arborescence »). Un import de 200 objets
// faisait de « Publier mon brouillon » une liste plate de 200 lignes.

export interface DraftFile {
  path: string
  state: string
}

export interface DirNode {
  /** Libellé affiché : plusieurs segments quand des répertoires sans fichier propre
   * et à un seul sous-répertoire sont fusionnés (« cartographie/imports »). */
  name: string
  /** Chemin complet du répertoire, clé stable. */
  path: string
  dirs: DirNode[]
  files: DraftFile[]
}

/** Au-delà, un répertoire est replié à l'ouverture : ses fichiers ne noient pas la liste. */
export const FOLDED_ABOVE = 20

export function buildTree(files: readonly DraftFile[]): DirNode {
  const root: DirNode = { name: '', path: '', dirs: [], files: [] }
  for (const file of files) {
    const parts = file.path.split('/')
    let node = root
    for (const part of parts.slice(0, -1)) {
      const path = node.path ? `${node.path}/${part}` : part
      let next = node.dirs.find((d) => d.path === path)
      if (!next) {
        next = { name: part, path, dirs: [], files: [] }
        node.dirs.push(next)
      }
      node = next
    }
    node.files.push(file)
  }
  return sortAndFold(root)
}

function sortAndFold(node: DirNode): DirNode {
  let current = node
  // La racine ne se fusionne pas : elle n'est pas affichée.
  while (current.path !== '' && current.files.length === 0 && current.dirs.length === 1) {
    const only = current.dirs[0]
    current = { ...only, name: `${current.name}/${only.name}` }
  }
  return {
    ...current,
    dirs: current.dirs.map(sortAndFold).sort((a, b) => a.name.localeCompare(b.name)),
    files: [...current.files].sort((a, b) => a.path.localeCompare(b.path)),
  }
}

function allFiles(node: DirNode): DraftFile[] {
  return [...node.files, ...node.dirs.flatMap(allFiles)]
}

function basename(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1)
}

export function DraftTree({
  files,
  conflicts,
  onOpen,
}: {
  files: readonly DraftFile[]
  conflicts: ReadonlySet<string>
  onOpen: (path: string) => void
}) {
  const { t } = useTranslation()
  const root = buildTree(files)
  return (
    <ul className="draft-tree" aria-label={t('draftTreeLabel')}>
      <Children node={root} conflicts={conflicts} onOpen={onOpen} />
    </ul>
  )
}

function Children({ node, conflicts, onOpen }: { node: DirNode; conflicts: ReadonlySet<string>; onOpen: (path: string) => void }) {
  const { t } = useTranslation()
  return (
    <>
      {node.dirs.map((d) => (
        <Dir key={d.path} node={d} conflicts={conflicts} onOpen={onOpen} />
      ))}
      {node.files.map((f) => (
        <li key={f.path} className="draft-tree-file row">
          <button type="button" className="btn-link" title={f.path} onClick={() => onOpen(f.path)}>
            <code>{basename(f.path)}</code>
          </button>
          <span className="pill">{t(`draftState_${f.state}`)}</span>
          {conflicts.has(f.path) ? <span className="badge-warn">{t('publishConflict')}</span> : null}
        </li>
      ))}
    </>
  )
}

function Dir({ node, conflicts, onOpen }: { node: DirNode; conflicts: ReadonlySet<string>; onOpen: (path: string) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(node.files.length <= FOLDED_ABOVE)
  const inside = allFiles(node)
  const counts = new Map<string, number>()
  for (const f of inside) counts.set(f.state, (counts.get(f.state) ?? 0) + 1)
  const conflicted = inside.some((f) => conflicts.has(f.path))
  return (
    <li className="draft-tree-dir">
      <button
        type="button"
        className="draft-tree-toggle row"
        aria-expanded={open}
        aria-label={t(open ? 'draftTreeFold' : 'draftTreeUnfold', { name: node.name })}
        onClick={() => setOpen(!open)}
      >
        <span className="draft-tree-caret" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <code className="draft-tree-name">{node.name}/</code>
        <span className="muted small">
          {t('draftTreeCount', { count: inside.length })}
          {[...counts.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([state, n]) => ` · ${n} ${t(`draftState_${state}`)}`)
            .join('')}
        </span>
        {conflicted ? <span className="badge-warn">{t('publishConflict')}</span> : null}
      </button>
      {open ? (
        <ul>
          <Children node={node} conflicts={conflicts} onOpen={onOpen} />
        </ul>
      ) : null}
    </li>
  )
}
