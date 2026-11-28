import { type ReactNode, useState } from 'react'
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

/** `base` : répertoire déjà nommé par le bloc qui contient l'arbre, retiré des
 * chemins affichés (ticket 9774a263) ; les fichiers gardent leur chemin complet. */
export function buildTree(files: readonly DraftFile[], base = ''): DirNode {
  const root: DirNode = { name: '', path: base, dirs: [], files: [] }
  for (const file of files) {
    const parts = (base && file.path.startsWith(`${base}/`) ? file.path.slice(base.length + 1) : file.path).split('/')
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
  while (current.name !== '' && current.files.length === 0 && current.dirs.length === 1) {
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
  base = '',
  label,
}: {
  files: readonly DraftFile[]
  conflicts: ReadonlySet<string>
  onOpen: (path: string) => void
  base?: string
  label?: string
}) {
  const { t } = useTranslation()
  const root = buildTree(files, base)
  return (
    <ul className="draft-tree" aria-label={label ?? t('draftTreeLabel')}>
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
        <Summary files={allFiles(node)} conflicts={conflicts} />
      </button>
      {open ? (
        <ul>
          <Children node={node} conflicts={conflicts} onOpen={onOpen} />
        </ul>
      ) : null}
    </li>
  )
}

/** Nombre de fichiers, décompte par état, et conflit (visible même replié). */
function Summary({ files, conflicts }: { files: readonly DraftFile[]; conflicts: ReadonlySet<string> }) {
  const { t } = useTranslation()
  const counts = new Map<string, number>()
  for (const f of files) counts.set(f.state, (counts.get(f.state) ?? 0) + 1)
  const conflicted = files.some((f) => conflicts.has(f.path))
  return (
    <>
      <span className="muted small">
        {t('draftTreeCount', { count: files.length })}
        {[...counts.entries()]
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([state, n]) => ` · ${n} ${t(`draftState_${state}`)}`)
          .join('')}
      </span>
      {conflicted ? <span className="badge-warn">{t('publishConflict')}</span> : null}
    </>
  )
}

// Blocs de l'écran de publication (ticket 9774a263, Gael : « couper l'arbre en
// blocs avec chacun son arbre ; quand tu ouvres la page chaque bloc est
// collapsé »), d'après le premier répertoire du chemin.
const BLOCKS: readonly { key: string; dir: string | null }[] = [
  { key: 'cartographie', dir: 'cartographie' },
  { key: 'domaines', dir: 'domaines' },
  { key: 'governance', dir: 'governance' },
  { key: 'referentiels', dir: 'referentiels' },
  { key: 'sources', dir: 'sources' },
  { key: 'other', dir: null },
]

export function DraftBlocks({
  files,
  conflicts,
  onOpen,
  content,
}: {
  files: readonly DraftFile[]
  conflicts: ReadonlySet<string>
  onOpen: (path: string) => void
  /** Contenu propre d'un bloc à la place de son arbre, rendu à l'ouverture
   * seulement (ticket 173c6ed6 : la vue par source des « Sources ») ; reçoit
   * l'arbre, comme repli. */
  content?: Partial<Record<string, (tree: ReactNode) => ReactNode>>
}) {
  // Un fichier à la racine du dépôt, ou sous un répertoire non listé : « Autres ».
  const blockOf = (path: string) =>
    BLOCKS.find((b) => b.dir !== null && path.startsWith(`${b.dir}/`))?.key ?? 'other'
  return (
    <div className="stack stack-tight draft-blocks">
      {BLOCKS.map((b) => {
        const mine = files.filter((f) => blockOf(f.path) === b.key)
        return mine.length > 0 ? (
          <Block
            key={b.key}
            name={b.key}
            dir={b.dir ?? ''}
            files={mine}
            conflicts={conflicts}
            onOpen={onOpen}
            content={content?.[b.key]}
          />
        ) : null
      })}
    </div>
  )
}

function Block({
  name,
  dir,
  files,
  conflicts,
  onOpen,
  content,
}: {
  name: string
  dir: string
  files: readonly DraftFile[]
  conflicts: ReadonlySet<string>
  onOpen: (path: string) => void
  content?: (tree: ReactNode) => ReactNode
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const title = t(`draftBlock_${name}`)
  return (
    <section className="panel draft-block">
      <button
        type="button"
        className="draft-tree-toggle draft-block-head row"
        aria-expanded={open}
        aria-label={t(open ? 'draftTreeFold' : 'draftTreeUnfold', { name: title })}
        onClick={() => setOpen(!open)}
      >
        <span className="draft-tree-caret" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <span className="draft-block-title">{title}</span>
        <Summary files={files} conflicts={conflicts} />
      </button>
      {open
        ? (() => {
            const tree = <DraftTree files={files} conflicts={conflicts} onOpen={onOpen} base={dir} label={title} />
            return content ? <div className="draft-block-body">{content(tree)}</div> : tree
          })()
        : null}
    </section>
  )
}
