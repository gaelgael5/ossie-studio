import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { api } from '../apiClient'
import type { CartoProcessCheck, CartoProcessSummary } from '../apiClient'
import { errorMessages } from '../errors'
import { BusyButton } from '../components/BusyButton'
import { ErrorList } from '../components/ErrorList'
import { ProcessPreview } from './ProcessPreview'
import { cartoProcessPath } from './ProcessesTab'

// Fenêtres de l'onglet Processus (ticket 7e061c3e, maquette validée par Gael) : créer un
// processus, importer un fichier BPMN produit par un autre outil (vérifications et
// aperçu avant d'écrire ; le fichier est gardé tel quel), rattacher un processus au
// processus métier dont il est le détail.

const KEY_FORMAT = /^[A-Z0-9]+(?:-[A-Z0-9]+)*$/
const PROCESSES_DIR = 'cartographie/processus'

/** Un processus métier proposable : objet dont le type repose sur `BusinessProcess`. */
export interface BusinessProcessChoice {
  key: string
  name: string
}

const INVALIDATED = ['carto-processes', 'carto-process', 'carto-object', 'draft', 'draftChanges']

function useInvalidate() {
  const queryClient = useQueryClient()
  return () => Promise.all(INVALIDATED.map((k) => queryClient.invalidateQueries({ queryKey: [k] })))
}

function KeyField({ value, onChange, taken }: { value: string; onChange: (v: string) => void; taken: Set<string> }) {
  const { t } = useTranslation()
  const error = !value ? null : !KEY_FORMAT.test(value) ? t('cartoKeyFormat') : taken.has(value) ? t('cartoProcessKeyTaken') : null
  return (
    <div className="field">
      <label className="field-label" htmlFor="carto-process-key">
        {t('cartoKey')}
      </label>
      <input
        id="carto-process-key"
        className="input mono"
        value={value}
        placeholder="PRC-PRISE-COMMANDE-DETAIL"
        onChange={(e) => onChange(e.target.value.toUpperCase())}
      />
      {error ? <span className="field-error">{error}</span> : null}
      {value && !error ? (
        <span className="field-hint">{t('cartoProcessStoredIn', { path: `${PROCESSES_DIR}/${value.toLowerCase()}.bpmn` })}</span>
      ) : null}
    </div>
  )
}

function DetailField({
  value,
  onChange,
  choices,
  hint,
}: {
  value: string
  onChange: (v: string) => void
  choices: BusinessProcessChoice[]
  hint: string
}) {
  const { t } = useTranslation()
  return (
    <div className="field">
      <label className="field-label" htmlFor="carto-process-detail">
        {t('cartoProcessDetailField')}
      </label>
      <select id="carto-process-detail" className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t('cartoProcessChooseDetail')}</option>
        {choices.map((c) => (
          <option key={c.key} value={c.key}>
            {`${c.name} (${c.key})`}
          </option>
        ))}
      </select>
      <span className="field-hint">{hint}</span>
    </div>
  )
}

function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const { t } = useTranslation()
  return (
    <div role="dialog" aria-label={title} className="dialog-backdrop">
      <div className="panel dialog-panel stack">
        <div className="row dialog-head">
          <h2>{title}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function NewProcessDialog({
  spaceId,
  branchId,
  choices,
  taken,
  initialDetail = '',
  onClose,
}: {
  spaceId: string
  branchId: string
  choices: BusinessProcessChoice[]
  taken: Set<string>
  initialDetail?: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const invalidate = useInvalidate()
  const initialName = choices.find((c) => c.key === initialDetail)?.name
  const [key, setKey] = useState(initialDetail ? `${initialDetail}-DETAIL` : '')
  const [name, setName] = useState(initialName ? t('cartoProcessDetailName', { name: initialName }) : '')
  const [detail, setDetail] = useState(initialDetail)
  const ready = KEY_FORMAT.test(key) && !taken.has(key) && name.trim() !== ''
  const create = useMutation({
    mutationFn: () => api.createCartoProcess(spaceId, branchId, { key, name: name.trim(), detail_of: detail || null }),
    onSuccess: async (created) => {
      await invalidate()
      onClose()
      navigate(cartoProcessPath(created.key))
    },
  })
  return (
    <Dialog title={t('cartoNewProcessTitle')} onClose={onClose}>
      <ErrorList messages={create.error ? errorMessages(t, create.error) : []} />
      <KeyField value={key} onChange={setKey} taken={taken} />
      <div className="field">
        <label className="field-label" htmlFor="carto-process-name">
          {t('cartoName')}
        </label>
        <input id="carto-process-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <DetailField value={detail} onChange={setDetail} choices={choices} hint={t('cartoProcessDetailHintNew')} />
      <div className="row modal-foot">
        <button type="button" className="btn" onClick={onClose}>
          {t('cancelEdit')}
        </button>
        <BusyButton
          type="button"
          className="btn btn-primary"
          busy={create.isPending}
          busyText={t('cartoModelCreateBusy')}
          disabled={!ready}
          onClick={() => create.mutate()}
        >
          {t('cartoModelCreate')}
        </BusyButton>
      </div>
    </Dialog>
  )
}

function Check({ state, children }: { state: 'ok' | 'warn' | 'ko'; children: React.ReactNode }) {
  const mark = { ok: '✓', warn: '!', ko: '✗' }[state]
  return (
    <li className={`carto-check carto-check-${state}`}>
      <span className="carto-check-mark" aria-hidden="true">
        {mark}
      </span>
      {children}
    </li>
  )
}

export function ImportProcessDialog({
  spaceId,
  branchId,
  choices,
  taken,
  onClose,
}: {
  spaceId: string
  branchId: string
  choices: BusinessProcessChoice[]
  taken: Set<string>
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const invalidate = useInvalidate()
  const [file, setFile] = useState<{ name: string; size: number; content: string } | null>(null)
  const [checked, setChecked] = useState<CartoProcessCheck | null>(null)
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [detail, setDetail] = useState('')
  const check = useMutation({
    mutationFn: (chosen: { name: string; content: string }) =>
      api.checkCartoProcessImport(spaceId, branchId, { content: chosen.content, filename: chosen.name }),
    onSuccess: (result) => {
      setChecked(result)
      setKey(result.suggested_key ?? '')
      setName(result.name ?? '')
    },
  })
  const importing = useMutation({
    mutationFn: () =>
      api.importCartoProcess(spaceId, branchId, {
        key,
        name: name.trim(),
        detail_of: detail || null,
        content: file?.content ?? '',
      }),
    onSuccess: async (imported) => {
      await invalidate()
      onClose()
      navigate(cartoProcessPath(imported.key))
    },
  })
  const choose = async (chosen: File | undefined) => {
    if (!chosen) return
    const content = await chosen.text()
    setFile({ name: chosen.name, size: chosen.size, content })
    setChecked(null)
    check.mutate({ name: chosen.name, content })
  }
  const importable = Boolean(checked?.valid && checked.has_diagram && KEY_FORMAT.test(key) && !taken.has(key))
  return (
    <div role="dialog" aria-label={t('cartoImportProcessTitle')} className="dialog-backdrop">
      <div className="panel dialog-panel dialog-wide stack">
        <div className="row dialog-head">
          <h2>{t('cartoImportProcessTitle')}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label={t('close')}>
            {'✕'}
          </button>
        </div>
        <ErrorList messages={[check.error, importing.error].flatMap((e) => (e ? errorMessages(t, e) : []))} />
        <div className="carto-import">
          <div className="stack">
            <label className="carto-dropzone">
              <input
                type="file"
                accept=".bpmn,.xml,application/xml,text/xml"
                className="sr-only"
                aria-label={t('cartoImportChooseFile')}
                onChange={(e) => void choose(e.target.files?.[0])}
              />
              <span className="carto-file-badge mono">{'.bpmn'}</span>
              {file ? (
                <span className="stack-tight">
                  <strong>{file.name}</strong>
                  <span className="muted small">
                    {t('cartoImportFileSize', { size: Math.max(1, Math.round(file.size / 1024)) })} ·{' '}
                    <span className="carto-link">{t('cartoImportChange')}</span>
                  </span>
                </span>
              ) : (
                <span>{t('cartoImportChooseFile')}</span>
              )}
            </label>
            {checked ? (
              <section aria-label={t('cartoImportChecks')}>
                <h3 className="carto-detail-title">{t('cartoImportChecks')}</h3>
                <ul className="carto-checks">
                  {checked.valid ? (
                    <Check state="ok">{t('cartoImportValid')}</Check>
                  ) : (
                    <Check state="ko">
                      {t('cartoImportInvalid', { detail: checked.error?.detail ?? checked.error?.code ?? '' })}
                    </Check>
                  )}
                  {checked.valid ? (
                    checked.has_diagram ? (
                      <Check state="ok">{t('cartoImportDiagram')}</Check>
                    ) : (
                      <Check state="ko">{t('cartoImportNoDiagram')}</Check>
                    )
                  ) : null}
                  {checked.valid ? (
                    <Check state="ok">
                      {t('cartoImportCounts', {
                        participants: t('cartoParticipantsCount', { count: checked.participants }),
                        lanes: t('cartoLanesCount', { count: checked.lanes }),
                        elements: t('cartoElementsCount', { count: checked.elements }),
                        flows: t('cartoFlowsCount', { count: checked.flows }),
                      })}
                    </Check>
                  ) : null}
                  {checked.valid ? (
                    checked.attached ? (
                      <Check state="ok">{t('cartoImportAttached', { count: checked.attached })}</Check>
                    ) : (
                      <Check state="warn">{t('cartoImportNothingAttached')}</Check>
                    )
                  ) : null}
                </ul>
              </section>
            ) : null}
            {checked?.valid ? (
              <>
                <KeyField value={key} onChange={setKey} taken={taken} />
                <div className="field">
                  <label className="field-label" htmlFor="carto-process-name">
                    {t('cartoName')}
                  </label>
                  <input id="carto-process-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
                  <span className="field-hint">{t('cartoImportNameHint')}</span>
                </div>
                <DetailField value={detail} onChange={setDetail} choices={choices} hint={t('cartoProcessDetailHintImport')} />
              </>
            ) : null}
          </div>
          <div className="stack">
            <h3 className="carto-detail-title">{t('cartoImportPreview')}</h3>
            <div className="carto-import-preview">
              {checked?.preview ? <ProcessPreview preview={checked.preview} width={600} height={280} labels /> : null}
            </div>
            <p className="info-note">{t('cartoImportKeptAsIs')}</p>
          </div>
        </div>
        <div className="row modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            {t('cancelEdit')}
          </button>
          <BusyButton
            type="button"
            className="btn btn-primary"
            busy={importing.isPending}
            busyText={t('cartoImportBusy')}
            disabled={!importable}
            onClick={() => importing.mutate()}
          >
            {t('cartoImportAndOpen')}
          </BusyButton>
        </div>
      </div>
    </div>
  )
}

export function AttachProcessDialog({
  spaceId,
  branchId,
  process,
  choices,
  onClose,
}: {
  spaceId: string
  branchId: string
  process: CartoProcessSummary
  choices: BusinessProcessChoice[]
  onClose: () => void
}) {
  const { t } = useTranslation()
  const invalidate = useInvalidate()
  const [detail, setDetail] = useState(process.detail_of?.key ?? '')
  const attach = useMutation({
    mutationFn: () => api.attachCartoProcess(spaceId, branchId, process.key, detail || null),
    onSuccess: async () => {
      await invalidate()
      onClose()
    },
  })
  return (
    <Dialog title={t('cartoAttachProcessTitle', { name: process.name })} onClose={onClose}>
      <ErrorList messages={attach.error ? errorMessages(t, attach.error) : []} />
      <DetailField value={detail} onChange={setDetail} choices={choices} hint={t('cartoAttachProcessHint')} />
      <div className="row modal-foot">
        <button type="button" className="btn" onClick={onClose}>
          {t('cancelEdit')}
        </button>
        <BusyButton
          type="button"
          className="btn btn-primary"
          busy={attach.isPending}
          busyText={t('cartoAttachBusy')}
          disabled={!detail}
          onClick={() => attach.mutate()}
        >
          {t('cartoProcessAttachConfirm')}
        </BusyButton>
      </div>
    </Dialog>
  )
}
