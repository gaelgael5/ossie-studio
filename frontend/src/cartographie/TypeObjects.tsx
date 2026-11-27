import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

// Onglet « Résumé » d'un type (ticket 7dc220a4) : ses objets, clé et nom ; la ligne
// cliquée montre à droite le fichier YAML de l'objet tel qu'il est stocké. Gael :
// « pour le moment l'objet yaml » — lecture seule, l'édition reste sur la fiche.
export function TypeObjects({ spaceId, branchId, slug }: { spaceId: string; branchId: string; slug: string }) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<string | null>(null)
  // Même requête (et même cache) que la liste de la cartographie : tous les objets
  // de l'espace, filtrés ici sur le type.
  const objects = useQuery({
    queryKey: ['carto-objects', spaceId, branchId],
    queryFn: () => api.listCartoObjects(spaceId, branchId),
  })
  const mine = useMemo(
    () =>
      (objects.data?.objects ?? [])
        .filter((o) => o.type === slug)
        .sort((a, b) => a.key.localeCompare(b.key)),
    [objects.data, slug],
  )
  const detail = useQuery({
    queryKey: ['carto-object', spaceId, branchId, selected],
    queryFn: () => api.getCartoObject(spaceId, branchId, selected ?? ''),
    enabled: selected !== null,
  })
  return (
    <section className="stack carto-type-objects" aria-label={t('cartoTypeObjects')}>
      <h3>{t('cartoTypeObjectsCount', { count: mine.length })}</h3>
      <ErrorList messages={objects.error ? errorMessages(t, objects.error) : []} />
      {objects.isPending ? (
        <p className="muted">{t('loading')}</p>
      ) : mine.length === 0 ? (
        <p className="empty-note">{t('cartoTypeObjectsEmpty')}</p>
      ) : (
        <div className="carto-type-objects-layout">
          {/* La liste défile seule : avec plusieurs centaines d'objets, le YAML
              reste à côté de la ligne choisie. */}
          <div className="panel table-wrap carto-type-objects-list">
            <table className="table" aria-label={t('cartoTypeObjects')}>
              <thead>
                <tr>
                  <th>{t('cartoTypeObjectKey')}</th>
                  <th>{t('cartoTypeObjectName')}</th>
                </tr>
              </thead>
              <tbody>
                {mine.map((o) => (
                  <tr
                    key={o.key}
                    className={`row-clickable${selected === o.key ? ' selected' : ''}`}
                    tabIndex={0}
                    aria-selected={selected === o.key}
                    onClick={() => setSelected(o.key)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && e.target === e.currentTarget) setSelected(o.key)
                    }}
                  >
                    <td className="mono">{o.key}</td>
                    <td>{o.name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="carto-type-objects-yaml">
            {selected === null ? (
              <p className="muted">{t('cartoTypeObjectsPick')}</p>
            ) : detail.isPending ? (
              <p className="muted">{t('loading')}</p>
            ) : detail.error ? (
              <ErrorList messages={errorMessages(t, detail.error)} />
            ) : (
              <pre className="code-block carto-type-object-yaml" aria-label={t('cartoTypeObjectYaml', { key: selected })}>
                {detail.data?.yaml ?? ''}
              </pre>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
