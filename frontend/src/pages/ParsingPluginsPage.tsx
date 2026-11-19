import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '../apiClient'
import type { ParsingPlugin, PluginParamItem } from '../apiClient'
import { errorMessages } from '../errors'
import { ErrorList } from '../components/ErrorList'

export function ParsingPluginsPage() {
  const { t } = useTranslation()
  const [messages, setMessages] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const plugins = useQuery({ queryKey: ['parsing-plugins'], queryFn: api.listParsingPlugins })

  return (
    <main>
      <div className="page-head">
        <div>
          <h1 className="page-title">{t('parsingPluginsTitle')}</h1>
          <p className="page-sub">{t('parsingPluginsSubtitle')}</p>
        </div>
      </div>
      <ErrorList messages={messages} />
      {notice ? <p role="status">{notice}</p> : null}
      <div className="stack">
        {(plugins.data?.plugins ?? []).map((plugin) => (
          <PluginPanel
            key={plugin.id}
            plugin={plugin}
            onSaved={(msg) => setNotice(msg)}
            onError={(msgs) => setMessages(msgs)}
          />
        ))}
      </div>
    </main>
  )
}

function PluginPanel({
  plugin,
  onSaved,
  onError,
}: {
  plugin: ParsingPlugin
  onSaved: (notice: string) => void
  onError: (messages: string[]) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <strong>{plugin.label}</strong>
          <span className="muted mono" style={{ marginLeft: 8, fontSize: '0.82rem' }}>
            {plugin.id} · {plugin.version}
          </span>
        </div>
        <span className="pill mono" style={{ fontSize: '0.8rem' }}>
          {plugin.cible}
        </span>
      </div>
      <p className="muted mono" style={{ fontSize: '0.82rem' }}>
        {t('colGlobs')} : {plugin.globs.join(', ')}
      </p>
      {plugin.parametres.length > 0 ? (
        <table className="table">
          <thead>
            <tr>
              <th>{t('colParam')}</th>
              <th>{t('colValue')}</th>
              <th aria-label={t('colActions')} />
            </tr>
          </thead>
          <tbody>
            {plugin.parametres.map((param) => (
              <ParamRow
                key={param.slug}
                pluginId={plugin.id}
                param={param}
                onSaved={() => onSaved(t('paramSaved'))}
                onError={onError}
              />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">{t('pluginNoParams')}</p>
      )}
    </div>
  )
}

function ParamRow({
  pluginId,
  param,
  onSaved,
  onError,
}: {
  pluginId: string
  param: PluginParamItem
  onSaved: () => void
  onError: (messages: string[]) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [value, setValue] = useState(param.value)
  useEffect(() => setValue(param.value), [param.value])

  const save = useMutation({
    mutationFn: () => api.setPluginParam(pluginId, param.slug, value),
    onSuccess: async () => {
      onSaved()
      await queryClient.invalidateQueries({ queryKey: ['parsing-plugins'] })
    },
    onError: (err) => onError(errorMessages(t, err)),
  })

  return (
    <tr>
      <td>
        <strong>{param.label}</strong>
        <div className="muted" style={{ fontSize: '0.82rem' }}>
          {param.doc}
        </div>
        <div className="muted mono" style={{ fontSize: '0.78rem' }}>
          {param.slug} · {param.type}
        </div>
      </td>
      <td>
        <input
          className="input mono"
          style={{ maxWidth: 200 }}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </td>
      <td>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={save.isPending || value === param.value}
          onClick={() => save.mutate()}
        >
          {t('saveValue')}
        </button>
      </td>
    </tr>
  )
}
