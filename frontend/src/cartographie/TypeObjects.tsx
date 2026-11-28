import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'
import { BusyButton } from '../components/BusyButton'
import { cartoObjectPath } from './ObjectsTab'
import { asciiLower } from '../slug'
import { IconButton, IconLink } from '../components/IconButton'

// Onglet « Résumé » d'un type (ticket 7dc220a4) : ses objets, clé et nom ; la ligne
// cliquée montre à droite le fichier YAML de l'objet tel qu'il est stocké. Gael :
// « pour le moment l'objet yaml » — lecture seule, l'édition reste sur la fiche.
// Ticket 61a24b85 : chaque ligne porte un crayon (vers la fiche) et une corbeille.
// Ticket b531abe2 : un filtre sur la clé et le nom (casse et accents ignorés).
export function TypeObjects({
  spaceId,
  branchId,
  slug,
  readOnly,
}: {
  spaceId: string
  branchId: string
  slug: string
  readOnly: boolean
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  // Confirmation sur la ligne plutôt qu'en fenêtre : le geste reste là où l'on a cliqué.
  const [confirming, setConfirming] = useState<string | null>(null)
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
  const [search, setSearch] = useState('')
  const needle = asciiLower(search.trim())
  const shown = needle ? mine.filter((o) => asciiLower(`${o.key} ${o.name}`).includes(needle)) : mine
  const detail = useQuery({
    queryKey: ['carto-object', spaceId, branchId, selected],
    queryFn: () => api.getCartoObject(spaceId, branchId, selected ?? ''),
    enabled: selected !== null,
  })
  // Même suppression que la fiche : l'API refuse un objet encore relié, le refus
  // s'affiche au-dessus de la liste.
  const remove = useMutation({
    mutationFn: (ref: string) => api.deleteCartoObject(spaceId, branchId, ref),
    onSuccess: async (_r, key) => {
      setConfirming(null)
      if (selected === key) setSelected(null)
      await Promise.all(
        ['carto-objects', 'carto-object', 'carto-types', 'carto-type', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
    },
  })
  // Version publiée : la liste est en lecture seule même pour qui peut écrire.
  const writable = !readOnly && objects.data?.readonly === false
  return (
    <section className="stack carto-type-objects" aria-label={t('cartoTypeObjects')}>
      <h3>
        {needle
          ? t('cartoTypeObjectsFiltered', { count: shown.length, total: mine.length })
          : t('cartoTypeObjectsCount', { count: mine.length })}
      </h3>
      <ErrorList
        messages={[
          ...(objects.error ? errorMessages(t, objects.error) : []),
          ...(remove.error ? errorMessages(t, remove.error) : []),
        ]}
      />
      {objects.isPending ? (
        <p className="muted">{t('loading')}</p>
      ) : mine.length === 0 ? (
        <p className="empty-note">{t('cartoTypeObjectsEmpty')}</p>
      ) : (
        <>
          <input
            type="search"
            className="input carto-type-objects-search"
            aria-label={t('cartoObjectSearch')}
            placeholder={t('cartoObjectSearch')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="carto-type-objects-layout">
            {/* La liste défile seule : avec plusieurs centaines d'objets, le YAML
                reste à côté de la ligne choisie. */}
            <div className="panel table-wrap carto-type-objects-list">
              <table className="table" aria-label={t('cartoTypeObjects')}>
                <thead>
                  <tr>
                    <th>{t('cartoTypeObjectKey')}</th>
                    <th>{t('cartoTypeObjectName')}</th>
                    <th>
                      <span className="sr-only">{t('cartoTypeObjectActions')}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="muted">
                        {t('cartoTypeObjectsNoMatch')}
                      </td>
                    </tr>
                  ) : null}
                  {shown.map((o) => (
                    <tr
                      key={o.ref}
                      className={`row-clickable${selected === o.ref ? ' selected' : ''}`}
                      tabIndex={0}
                      aria-selected={selected === o.ref}
                      onClick={() => setSelected(o.ref)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && e.target === e.currentTarget) setSelected(o.ref)
                      }}
                    >
                      <td className="mono">{o.key}</td>
                      <td>{o.name}</td>
                      {/* Les boutons arrêtent le clic : ouvrir ou supprimer ne sélectionne pas la ligne. */}
                      <td className="carto-type-object-actions" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                        {confirming === o.ref ? (
                          <>
                            <BusyButton
                              type="button"
                              className="btn btn-sm btn-danger"
                              busy={remove.isPending}
                              busyText={t('cartoDeleteBusy')}
                              onClick={() => remove.mutate(o.ref)}
                            >
                              {t('cartoDeleteObjectYes')}
                            </BusyButton>
                            <button type="button" className="btn btn-sm" onClick={() => setConfirming(null)}>
                              {t('cancelEdit')}
                            </button>
                          </>
                        ) : (
                          <>
                            <IconLink icon="edit" label={t('cartoTypeObjectEdit', { key: o.key })} to={cartoObjectPath(o.ref)} />
                            {writable ? (
                              <IconButton
                                icon="delete"
                                label={t('cartoTypeObjectDelete', { key: o.key })}
                                onClick={() => {
                                  remove.reset()
                                  setConfirming(o.ref)
                                }}
                              />
                            ) : null}
                          </>
                        )}
                      </td>
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
                <pre className="code-block carto-type-object-yaml" aria-label={t('cartoTypeObjectYaml', { key: selected.split('/').pop() })}>
                  {detail.data?.yaml ?? ''}
                </pre>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  )
}
