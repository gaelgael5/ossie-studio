import { useTranslation } from 'react-i18next'
import { diffRows } from '../mergeUtils'

// Correspondance des modifications : les propriétés qui diffèrent, côte à côte.
export function PropertyDiff({ left, right }: { left: unknown; right: unknown }) {
  const { t } = useTranslation()
  const rows = diffRows(left, right)
  if (rows.length === 0) return <div className="empty-note">{t('mergeNoDifference')}</div>
  return (
    <div className="panel table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{t('mergeColProperty')}</th>
            <th>{t('mergeColPublished')}</th>
            <th>{t('mergeColDraft')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.pointer}>
              <td>
                <code>{r.pointer}</code>
              </td>
              <td className="mono">{r.left}</td>
              <td className="mono">{r.right}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
