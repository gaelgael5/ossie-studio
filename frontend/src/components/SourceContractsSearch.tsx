import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { FoundContract, SourceItem } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from './BusyButton'
import { ErrorList } from './ErrorList'

// « Rechercher les contrats » d'une source Git (ticket docflow 56be38c6). Gael,
// 18/11/2026 : « dans la partie raw parsing de répertoire git il faut la
// possibilité de chercher et trouver les contrats openapi ». On voit, avant de
// collecter, ce que le parseur `openapi` prendrait ; rien n'est écrit.

/** Le parseur `openapi` tourne-t-il sur cette source ? Sans liste dans le
 * manifeste, tous les parseurs de code tournent (dont `openapi`). */
export function openapiChosen(source: SourceItem): boolean {
  const parsers = source.parameters?.parsers
  return !Array.isArray(parsers) || parsers.length === 0 || parsers.includes('openapi')
}

function StatusCell({ contract }: { contract: FoundContract }) {
  const { t } = useTranslation()
  if (contract.status === 'supported') return <span>{t('contractsSearchSupported')}</span>
  if (contract.status === 'unsupported') {
    return <span className="muted">{t('contractsSearchUnsupported')}</span>
  }
  return (
    <span className="text-danger">
      {t('contractsSearchInvalid')} <code>{contract.error}</code>
    </span>
  )
}

export function SourceContractsSearch({
  spaceId,
  branchId,
  source,
}: {
  spaceId: string
  branchId: string
  source: SourceItem
}) {
  const { t } = useTranslation()
  const [messages, setMessages] = useState<string[]>([])
  const search = useMutation({
    mutationFn: () => api.searchSourceContracts(spaceId, branchId, source.slug),
    onMutate: () => setMessages([]),
    onError: (err) => setMessages(errorMessages(t, err)),
  })
  const contracts = search.data?.contracts
  return (
    <section className="panel stack stack-tight" aria-label={t('contractsSearchTitle')}>
      <p className="panel-label">{t('contractsSearchTitle')}</p>
      <p className="muted small">{t('contractsSearchIntro')}</p>
      {!openapiChosen(source) ? <p className="muted small">{t('contractsSearchNotChosen')}</p> : null}
      <ErrorList messages={messages} />
      <div>
        <BusyButton
          type="button"
          className="btn btn-sm"
          busy={search.isPending}
          busyText={t('contractsSearchBusy')}
          onClick={() => search.mutate()}
        >
          {t('contractsSearchRun')}
        </BusyButton>
      </div>
      {contracts && contracts.length === 0 ? (
        <p className="muted">{t('contractsSearchNone')}</p>
      ) : null}
      {contracts && contracts.length > 0 ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t('contractsSearchPath')}</th>
                <th>{t('contractsSearchName')}</th>
                <th>{t('contractsSearchVersion')}</th>
                <th>{t('contractsSearchOpenapi')}</th>
                <th>{t('contractsSearchStatus')}</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((c) => (
                <tr key={c.path}>
                  <td className="mono">{c.path}</td>
                  <td>{c.title}</td>
                  <td className="mono">{c.version}</td>
                  <td className="mono">{c.openapi}</td>
                  <td>
                    <StatusCell contract={c} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}
