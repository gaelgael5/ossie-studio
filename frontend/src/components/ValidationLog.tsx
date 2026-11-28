import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '../apiClient'
import type { ValidationEntry } from '../apiClient'

// Journal de validation de la branche telle que je la vois (ticket d7139b17). Règle
// d'or : une erreur bloque la publication, qu'elle soit dans mon brouillon ou déjà
// dans la branche publiée ; un avertissement informe seulement (il ne s'accepte plus).
// Chaque entrée mène au document en cause quand l'écran en a une fiche.

/** La fiche du document en cause, si l'écran en a une. */
export function documentPath(entry: ValidationEntry): string | null {
  const id = entry.identity ?? {}
  const text = (name: string) => (typeof id[name] === 'string' ? encodeURIComponent(id[name] as string) : null)
  switch (entry.document_type) {
    case 'carto_objet':
      return text('type') && text('key') ? `/cartographie/objets/${text('type')}/${text('key')}` : null
    case 'carto_type':
      return text('slug') ? `/cartographie/types/${text('slug')}` : null
    case 'referentiel_modele':
    case 'referentiel_valeur':
      return text('referentiel') ? `/referentiels/${text('referentiel')}` : null
    default:
      return null
  }
}

function message(t: (key: string, options?: Record<string, unknown>) => string, e: ValidationEntry): string {
  if (e.rule === 'carto') {
    const what = t(`cartoProblem_${e.message_code}`, { defaultValue: e.message_code })
    return e.args.field ? `${e.args.field} · ${what}` : what
  }
  return t(`validation_${e.message_code}`, { ...e.args, defaultValue: e.message_code })
}

export function ValidationLog({ spaceId, branchId }: { spaceId: string; branchId: string }) {
  const { t } = useTranslation()
  const log = useQuery({
    queryKey: ['validation', spaceId, branchId],
    queryFn: () => api.draftValidation(spaceId, branchId),
  })
  const entries = log.data?.entries ?? []
  if (entries.length === 0) return null
  return (
    <div className="stack stack-tight">
      <p className="panel-label">{t('validationTitle', { count: log.data?.blocking ?? 0 })}</p>
      <ul className="list-plain stack stack-tight">
        {entries.map((e, i) => {
          const to = documentPath(e)
          return (
            <li key={`${e.rule}-${e.path}-${e.message_code}-${i}`} className="row">
              <span className={e.level === 'error' ? 'text-danger' : 'text-warn'}>{t(`validationLevel_${e.level}`)}</span>
              {to ? <Link to={to}><code>{e.path}</code></Link> : <code>{e.path}</code>}
              <span>{message(t, e)}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
