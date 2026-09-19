import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link } from 'react-router'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { DataTable, type Column } from '../../components/DataTable'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { fieldAria } from '../../components/fieldAria'
import { Icon, type IconName } from '../../components/Icon'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { TONE_CLASSES } from '../../components/stateStyles'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDateTime, formatInteger } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'
import { checkImportFile } from './importFile'

type ImportBatch = Schemas['ImportBatchOut']
type PreviewRow = Record<string, unknown>

const TEMPLATE_URL = '/api/v1/imports/templates/orders.csv'
const PREVIEW_COLUMNS = ['external_ref', 'customer_code', 'style_code', 'quantity', 'due_date', 'priority']

const BATCH_STATUS: Record<string, { label: string; icon: IconName; tone: keyof typeof TONE_CLASSES }> = {
  VALIDATED: { label: 'Validated', icon: 'check-circle', tone: 'success' },
  REJECTED: { label: 'Rejected', icon: 'x-circle', tone: 'danger' },
  COMMITTED: { label: 'Committed', icon: 'check-double', tone: 'success' },
}

function BatchStatus({ status }: { status: string }) {
  const style = BATCH_STATUS[status] ?? { label: status, icon: 'question' as const, tone: 'neutral' as const }
  return (
    <span
      className={`inline-flex h-6 items-center gap-1 rounded-full border px-2 text-xs font-medium ${TONE_CLASSES[style.tone]}`}
    >
      <Icon name={style.icon} className="h-3.5 w-3.5" />
      <span>{style.label}</span>
    </span>
  )
}

function previewCell(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') return formatInteger(value)
  if (typeof value === 'string') return /^\d+$/.test(value) ? formatInteger(Number(value)) : value
  return JSON.stringify(value)
}

const PREVIEW_TABLE_COLUMNS: Column<{ index: number; row: PreviewRow }>[] = PREVIEW_COLUMNS.map((key) => ({
  key,
  header: key,
  align: key === 'quantity' || key === 'priority' ? 'right' : 'left',
  render: ({ row }) => previewCell(row[key]),
}))

const ERROR_COLUMNS: Column<ImportBatch['errors'][number] & { index: number }>[] = [
  {
    key: 'row',
    header: 'Row',
    render: (error) => (error.row_number === 0 ? 'File' : formatInteger(error.row_number)),
  },
  { key: 'field', header: 'Field', render: (error) => <span className="font-mono text-xs">{error.field ?? 'Whole row'}</span> },
  { key: 'message', header: 'Problem', render: (error) => error.message },
]

function OrderImport() {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const uploadKey = useIdempotencyKey()
  const commitKey = useIdempotencyKey()
  const inputId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const upload = useMutation({
    mutationFn: ({ selected, key }: { selected: File; key: string }) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/imports/orders', {
          params: { path: { factory_id: factory.id }, header: { 'Idempotency-Key': key } },
          // OpenAPI describes the binary part as a string; the serializer sends the File itself.
          body: { file: selected.name },
          bodySerializer: () => {
            const form = new FormData()
            form.append('file', selected)
            return form
          },
        }),
      ),
    onSuccess: (result) => {
      uploadKey.reset()
      setBatch(result)
    },
  })

  const commit = useMutation({
    mutationFn: ({ batchId, key }: { batchId: string; key: string }) =>
      unwrap(
        api.POST('/api/v1/imports/{batch_id}/commit', {
          params: { path: { batch_id: batchId }, header: { 'Idempotency-Key': key } },
        }),
      ),
    onSuccess: async (result) => {
      commitKey.reset()
      setBatch(result)
      setConfirmOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['orders', factory.id] })
    },
    onError: () => {
      setConfirmOpen(false)
    },
  })

  const canCommit = batch?.status === 'VALIDATED' && !commit.isPending
  const committed = batch?.status === 'COMMITTED'

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="import-step-file" className="panel flex flex-col gap-4 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="import-step-file" className="text-base font-semibold">
            1. Choose a file
          </h2>
          <a className="btn-secondary" href={TEMPLATE_URL} download="orders-template.csv">
            <Icon name="download" />
            Download template
          </a>
        </div>
        <FormField
          id={inputId}
          label="CSV file"
          hint="Columns: external_ref, customer_code, style_code, quantity, due_date, priority. Up to 1 MB."
          error={fileError ?? undefined}
        >
          <input
            type="file"
            accept=".csv,text/csv"
            className="block w-full text-sm file:mr-3 file:h-9 file:rounded-md file:border file:border-line-strong/60 file:bg-surface file:px-3 file:text-sm file:font-medium file:text-fg"
            {...fieldAria(inputId, fileError ?? undefined, 'hint')}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null
              setBatch(null)
              upload.reset()
              commit.reset()
              setFile(selected)
              setFileError(selected ? checkImportFile(selected) : null)
            }}
          />
        </FormField>
        <div>
          <button
            type="button"
            className="btn-primary"
            disabled={!file || fileError !== null || upload.isPending}
            onClick={() => {
              if (!file) return
              const key = uploadKey.keyFor({ name: file.name, size: file.size, modified: file.lastModified })
              upload.mutate({ selected: file, key })
            }}
          >
            <Icon name="upload" />
            {upload.isPending ? 'Validating…' : 'Validate file'}
          </button>
        </div>
        {upload.isError && <ErrorState title="The file could not be validated" error={upload.error} />}
      </section>

      <section aria-labelledby="import-step-review" className="panel flex flex-col gap-4 p-6">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="import-step-review" className="text-base font-semibold">
            2. Review and commit
          </h2>
          {batch && <BatchStatus status={batch.status} />}
        </div>
        {!batch && <p className="text-fg-muted">Validate a file to see its rows here. Nothing is saved until you commit.</p>}
        {batch && (
          <p className="text-fg-muted">
            {formatInteger(batch.row_count)} rows read, {formatInteger(batch.errors.length)} problems found.
          </p>
        )}
        {batch && batch.errors.length > 0 && (
          <DataTable
            caption="Row errors"
            columns={ERROR_COLUMNS}
            rows={batch.errors.map((error, index) => ({ ...error, index }))}
            rowKey={(error) => String(error.index)}
          />
        )}
        {batch && batch.preview.length > 0 && (
          <DataTable
            caption="Import preview"
            columns={PREVIEW_TABLE_COLUMNS}
            rows={batch.preview.map((row, index) => ({ index, row }))}
            rowKey={(item) => String(item.index)}
          />
        )}
        {commit.isError && <ErrorState title="The import was not committed" error={commit.error} />}
        {committed && (
          <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border border-ok-line bg-ok-bg px-4 py-3 text-ok-fg">
            <Icon name="check-double" />
            <span className="font-semibold">{formatInteger(batch.row_count)} orders imported.</span>
            {batch.committed_at && <span>Committed {formatDateTime(batch.committed_at, factory.timezone)}.</span>}
            <Link className="link ml-auto" to={`/f/${encodeURIComponent(factory.code)}/orders`}>
              View orders
            </Link>
          </div>
        )}
        <div>
          <button
            type="button"
            className="btn-primary"
            disabled={!canCommit}
            onClick={() => {
              setConfirmOpen(true)
            }}
          >
            Commit import
          </button>
        </div>
      </section>

      {batch && (
        <ConfirmDialog
          open={confirmOpen}
          title={`Create ${formatInteger(batch.row_count)} orders?`}
          confirmLabel={commit.isPending ? 'Creating…' : 'Create orders'}
          pending={commit.isPending}
          onCancel={() => {
            setConfirmOpen(false)
          }}
          onConfirm={() => {
            commit.mutate({ batchId: batch.batch_id, key: commitKey.keyFor({ batch_id: batch.batch_id }) })
          }}
        >
          All rows are checked again before any order is created. If any row is no longer valid,
          no orders are created.
        </ConfirmDialog>
      )}
    </div>
  )
}

export function OrderImportPage() {
  const can = useCan()
  return (
    <>
      <PageHeader
        title="Import orders"
        description="Upload a CSV file, check the preview, then commit to create draft orders."
      />
      {can('order:import') ? (
        <OrderImport />
      ) : (
        <PermissionDenied message="Only planners and supervisors can import orders." />
      )}
    </>
  )
}
