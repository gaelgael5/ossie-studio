import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useUnsaved } from './unsaved'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { ReferentielProblem } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import type { RefModel } from './modelRules'
import { type FieldProblems, ValuePanel } from './ValuePanel'
import { SplitPane } from '../components/SplitPane'
import {
  type Value,
  compareByKey,
  isSimple,
  kindOf,
  matchesFilter,
  matchesSearch,
  propertiesOf,
  shown,
} from './valueRules'

// Onglet Valeurs (ticket fbe04555). Les valeurs appliquées s'accumulent jusqu'à
// « Enregistrer », qui écrit la liste complète par la brique « format ».

export const PAGE_SIZE = 50

interface Row {
  uid: string
  value: Value
  // Rang dans les valeurs enregistrées ; null : valeur nouvelle.
  origin: number | null
  modified: boolean
  deleted: boolean
}

interface Panel {
  uid: string | null
  draft: Value
}

function rowsOf(values: Value[]): Row[] {
  return values.map((value, i) => ({ uid: `o${i}`, value, origin: i, modified: false, deleted: false }))
}

export function ValuesTab({
  spaceId,
  branchId,
  name,
  model,
  readOnly,
  onErrorCount,
  initialQuery = '',
}: {
  spaceId: string
  branchId: string
  name: string
  model: RefModel
  readOnly: boolean
  onErrorCount: (count: number) => void
  /** La recherche de départ : le lien « Voir dans le référentiel » d'une fiche
   * de cartographie y met la valeur (ticket 43478978). */
  initialQuery?: string
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const key = Array.isArray(model['x-key']) ? model['x-key'] : []
  const columns = propertiesOf(model).filter(([, s]) => isSimple(s))
  const complex = propertiesOf(model).filter(([, s]) => !isSimple(s))
  const loaded = useQuery({
    queryKey: ['referentielValues', spaceId, branchId, name],
    queryFn: () => api.getReferentielValues(spaceId, branchId, name),
  })
  const [rows, setRows] = useState<Row[]>([])
  const [panel, setPanel] = useState<Panel | null>(null)
  const [problems, setProblems] = useState<Map<string, FieldProblems>>(new Map())
  const [problemCount, setProblemCount] = useState(0)
  const [query, setQuery] = useState(initialQuery)
  const [filterProp, setFilterProp] = useState('')
  const [filterValue, setFilterValue] = useState('')
  const [page, setPage] = useState(0)
  const counter = useRef(0)

  useEffect(() => {
    if (loaded.data) setRows(rowsOf(loaded.data.values))
  }, [loaded.data])

  // La liste vérifiée : les valeurs non supprimées, la valeur ouverte à sa place.
  const checked = useMemo(() => {
    const list: { uid: string; value: Value }[] = []
    for (const row of rows) {
      if (row.deleted) continue
      list.push({ uid: row.uid, value: panel && panel.uid === row.uid ? panel.draft : row.value })
    }
    if (panel && panel.uid === null) list.push({ uid: 'new', value: panel.draft })
    return list
  }, [rows, panel])

  const dirty = rows.some((r) => r.modified || r.deleted || r.origin === null)

  useEffect(() => {
    if (!loaded.data) return
    const handle = setTimeout(() => {
      api
        .verifyReferentielValues(spaceId, branchId, name, checked.map((c) => c.value))
        .then((result) => {
          const byRow = new Map<string, FieldProblems>()
          let count = 0
          for (const p of result.problems as ReferentielProblem[]) {
            const target = p.index !== undefined ? checked[p.index] : undefined
            if (!target) continue
            const fields = byRow.get(target.uid) ?? new Map<string, string[]>()
            // Une clé en double se montre sur les champs de la clé.
            const where = p.code === 'key_duplicate' ? key : [p.field ?? '']
            for (const field of where) fields.set(field, [...(fields.get(field) ?? []), p.code])
            byRow.set(target.uid, fields)
            if (target.uid !== 'new') count += 1
          }
          setProblems(byRow)
          setProblemCount(count)
        })
        .catch(() => undefined)
    }, 300)
    return () => clearTimeout(handle)
    // `key` dérive du modèle, stable tant que le modèle l'est.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checked, loaded.data, spaceId, branchId, name])

  useEffect(() => onErrorCount(problemCount), [problemCount, onErrorCount])

  const save = useMutation({
    mutationFn: () =>
      api.saveReferentielValues(
        spaceId,
        branchId,
        name,
        rows.filter((r) => !r.deleted).map((r) => r.value),
      ),
    onSuccess: async () => {
      await Promise.all(
        ['referentielValues', 'referentiel', 'referentiels', 'draft', 'draftChanges'].map((k) =>
          queryClient.invalidateQueries({ queryKey: [k] }),
        ),
      )
    },
  })
  useUnsaved(
    'values',
    { label: t('refTab_values'), dirty: !readOnly && dirty, saveable: problemCount === 0 && panel === null },
    () => save.mutateAsync(),
  )

  const visible = useMemo(() => {
    const filtered = rows.filter(
      (r) =>
        matchesSearch(r.value, columns.map(([c]) => c), query) &&
        matchesFilter(r.value, filterProp, filterValue),
    )
    return [...filtered].sort((a, b) => compareByKey(a.value, b.value, key))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, query, filterProp, filterValue])
  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE))
  const current = Math.min(page, pages - 1)
  const shownRows = visible.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE)

  const apply = () => {
    if (!panel) return
    if (panel.uid === null) {
      counter.current += 1
      setRows((prev) => [
        ...prev,
        { uid: `n${counter.current}`, value: panel.draft, origin: null, modified: true, deleted: false },
      ])
    } else {
      setRows((prev) => prev.map((r) => (r.uid === panel.uid ? { ...r, value: panel.draft, modified: true } : r)))
    }
    setPanel(null)
  }
  const remove = () => {
    if (!panel) return
    if (panel.uid !== null) {
      setRows((prev) =>
        prev
          .filter((r) => !(r.uid === panel.uid && r.origin === null))
          .map((r) => (r.uid === panel.uid ? { ...r, deleted: true } : r)),
      )
    }
    setPanel(null)
  }
  const summary = (value: unknown, kind: string) => {
    if (kind === 'array') {
      const n = Array.isArray(value) ? value.length : 0
      return n === 0 ? t('refSummaryNone') : t('refSummaryItems', { count: n })
    }
    return value && typeof value === 'object' && Object.keys(value).length > 0 ? t('refSummaryObject') : t('refSummaryNone')
  }
  const changes = rows.filter((r) => r.modified || r.deleted || r.origin === null).length

  const table =
    rows.length > 0 ? (
      <div className="panel table-wrap">
        <table className="table">
          <thead>
            <tr>
              {columns.map(([c]) => (
                <th key={c}>
                  {c} {key.includes(c) ? <span className="badge">{t('refKeyBadge')}</span> : null}
                </th>
              ))}
              {complex.map(([c]) => (
                <th key={c}>{c}</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {shownRows.map((row) => {
              const errors = problems.get(row.uid)
              return (
                <tr
                  key={row.uid}
                  className={`row-clickable${row.deleted ? ' row-deleted' : ''}${errors ? ' row-error' : ''}`}
                  onClick={() => !row.deleted && setPanel({ uid: row.uid, draft: row.value })}
                >
                  {columns.map(([c]) => (
                    <td key={c} className={errors?.has(c) ? 'cell-error' : undefined}>
                      {shown(row.value[c])}
                    </td>
                  ))}
                  {complex.map(([c, s]) => (
                    <td key={c} className="muted">
                      {summary(row.value[c], kindOf(s))}
                    </td>
                  ))}
                  <td>
                    {row.origin === null ? <span className="badge badge-warn">{t('refRowNew')}</span> : null}
                    {row.modified && row.origin !== null ? <span className="badge badge-warn">{t('refRowModified')}</span> : null}
                    {row.deleted ? <span className="badge badge-muted">{t('refRowDeleted')}</span> : null}
                    {errors ? <span className="badge badge-danger">{t('refRowError')}</span> : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    ) : null
  return (
    <div className="stack">
      <ErrorList messages={loaded.error ? errorMessages(t, loaded.error) : save.error ? errorMessages(t, save.error) : []} />
      <div className="row ref-values-tools">
        <input
          className="input"
          type="search"
          aria-label={t('refValuesSearch')}
          placeholder={t('refValuesSearch')}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setPage(0)
          }}
        />
        <select className="input" aria-label={t('refValuesFilterProp')} value={filterProp} onChange={(e) => setFilterProp(e.target.value)}>
          <option value="">{t('refValuesAll')}</option>
          {columns.map(([c]) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {filterProp ? (
          <input
            className="input"
            aria-label={t('refValuesFilterValue')}
            value={filterValue}
            onChange={(e) => {
              setFilterValue(e.target.value)
              setPage(0)
            }}
          />
        ) : null}
        {!readOnly ? (
          <button type="button" className="btn btn-primary" onClick={() => setPanel({ uid: null, draft: {} })}>
            {t('refValuesAdd')}
          </button>
        ) : null}
      </div>
      {changes > 0 ? (
        <p className="empty-note" role="status">
          {t('refValuesUnsaved', { count: changes })}
        </p>
      ) : null}
      {loaded.isLoading ? <p className="muted">{t('loading')}</p> : null}
      {loaded.data && rows.length === 0 ? <p className="empty-note">{t('refValuesEmpty')}</p> : null}
      {/* Barre de séparation entre la table et la valeur ouverte (ticket f74c8e74). */}
      {panel ? (
        <SplitPane
          id="ref-values"
          defaultRatio={0.54}
          left={table}
          right={
            <ValuePanel
              key={panel.uid ?? 'new'}
              model={model}
              draft={panel.draft}
              isNew={panel.uid === null || rows.find((r) => r.uid === panel.uid)?.origin === null}
              readOnly={readOnly}
              problems={problems.get(panel.uid ?? 'new') ?? new Map()}
              onChange={(draft) => setPanel((p) => (p ? { ...p, draft } : p))}
              onApply={apply}
              onCancel={() => setPanel(null)}
              onDelete={remove}
            />
          }
        />
      ) : (
        <div className="ref-values-layout">
          {table}
        </div>
      )}
      {pages > 1 ? (
        <div className="row">
          <button type="button" className="btn btn-sm" disabled={current === 0} onClick={() => setPage(current - 1)}>
            {t('refValuesPrev')}
          </button>
          <span className="muted">{t('refValuesPage', { page: current + 1, pages, count: visible.length })}</span>
          <button type="button" className="btn btn-sm" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>
            {t('refValuesNext')}
          </button>
        </div>
      ) : null}
      {!readOnly ? (
        <div className="row">
          <button
            type="button"
            className="btn"
            disabled={!dirty}
            onClick={() => {
              setRows(rowsOf(loaded.data?.values ?? []))
              setPanel(null)
            }}
          >
            {t('refValuesCancelAll')}
          </button>
          <BusyButton
            type="button"
            className="btn btn-primary"
            busy={save.isPending}
            busyText={t('refValuesSaveBusy')}
            disabled={!dirty || problemCount > 0 || panel !== null}
            onClick={() => save.mutate()}
          >
            {t('refValuesSave')}
          </BusyButton>
        </div>
      ) : null}
    </div>
  )
}
