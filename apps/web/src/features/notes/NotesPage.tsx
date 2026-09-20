import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link } from 'react-router'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Pagination } from '../../components/Pagination'
import { PermissionDenied } from '../../components/PermissionDenied'
import { fieldAria } from '../../components/fieldAria'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDateTime, humanizeCode } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'

type NoteOut = Schemas['NoteOut']
type EntityMentionOut = Schemas['EntityMentionOut']

const MAX_NOTE_LENGTH = 2000
const MIN_NOTE_LENGTH = 3
const NOTES_PAGE_SIZE = 20
const CLASSIFICATIONS = ['planning', 'materials', 'ie', 'quality', 'unknown']

/** Where an entity mention links to, for the labels the app has a real screen for. */
function entityHref(mention: EntityMentionOut, factoryPath: string): string | null {
  // Every mention in `note.entities` is resolved (the backend only stores
  // resolved mentions), so `resolved_id` is always present here.
  if (mention.label === 'ORDER' && mention.resolved_id) return `${factoryPath}/orders/${mention.resolved_id}`
  if (mention.label === 'LINE') return `${factoryPath}/planning`
  if (mention.label === 'MATERIAL') return `${factoryPath}/materials`
  return null
}

function EntityChip({ mention, factoryPath }: { mention: EntityMentionOut; factoryPath: string }) {
  const href = entityHref(mention, factoryPath)
  const body = (
    <>
      <span className="font-medium">{humanizeCode(mention.label)}:</span> {mention.text}
    </>
  )
  if (href) {
    return (
      <Link
        to={href}
        className="link inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs no-underline hover:bg-surface-sunken"
      >
        {body}
      </Link>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-neutral-line bg-neutral-bg px-2 py-0.5 text-xs text-neutral-fg">
      {body}
    </span>
  )
}

function NoteResult({ note, factoryPath }: { note: NoteOut; factoryPath: string }) {
  return (
    <div className="panel flex flex-col gap-3 p-4" role="status" aria-label="Note saved">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 rounded-full border border-info-line bg-info-bg px-2 py-0.5 text-xs font-medium text-info-fg">
          {humanizeCode(note.classification)}
        </span>
        <span className="text-xs text-fg-muted">Classifier {note.classifier_version}</span>
      </div>
      <p className="whitespace-pre-wrap">{note.text}</p>
      {note.entities.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-fg-muted">Linked entities</p>
          <div className="flex flex-wrap gap-1.5">
            {note.entities.map((mention, index) => (
              <EntityChip key={`${mention.label}-${index}`} mention={mention} factoryPath={factoryPath} />
            ))}
          </div>
        </div>
      )}
      {note.unresolved.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-fg-muted">Unresolved order references</p>
          <p className="text-fg-muted">
            No matching order was found for {note.unresolved.map((mention) => mention.text).join(', ')}.
          </p>
        </div>
      )}
      <p className="text-xs text-fg-muted">{note.notice}</p>
    </div>
  )
}

function NoteForm({ onCreated }: { onCreated: (note: NoteOut) => void }) {
  const factory = useFactory()
  const textId = useId()
  const idempotency = useIdempotencyKey()
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: (key: string) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/notes', {
          params: { path: { factory_id: factory.id }, header: { 'Idempotency-Key': key } },
          body: { text },
        }),
      ),
    onSuccess: (note) => {
      idempotency.reset()
      setText('')
      setError(null)
      onCreated(note)
    },
  })

  const tooShort = text.length > 0 && text.length < MIN_NOTE_LENGTH
  const tooLong = text.length > MAX_NOTE_LENGTH
  const clientError = tooShort
    ? `Enter at least ${MIN_NOTE_LENGTH} characters.`
    : tooLong
      ? `The note is longer than ${MAX_NOTE_LENGTH} characters.`
      : null

  return (
    <form
      className="panel flex flex-col gap-4 p-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (clientError || text.trim().length === 0) {
          setError(clientError ?? 'Enter a note.')
          return
        }
        create.mutate(idempotency.keyFor({ text }))
      }}
    >
      <FormField
        id={textId}
        label="Add a note"
        hint={`${text.length.toLocaleString()} / ${MAX_NOTE_LENGTH} characters. Mentions of orders, lines and materials are linked automatically.`}
        error={error ?? clientError ?? undefined}
      >
        <textarea
          className="input min-h-28 resize-y"
          maxLength={MAX_NOTE_LENGTH + 200}
          value={text}
          {...fieldAria(textId, error ?? clientError ?? undefined, `${textId}-hint`)}
          onChange={(event) => {
            setText(event.target.value)
            setError(null)
          }}
        />
      </FormField>
      {create.isError && <ErrorState error={create.error} title="The note was not saved" />}
      <div>
        <button type="submit" className="btn-primary" disabled={create.isPending}>
          {create.isPending ? 'Saving…' : 'Save note'}
        </button>
      </div>
    </form>
  )
}

function columns(timeZone: string): Column<NoteOut>[] {
  return [
    {
      key: 'text',
      header: 'Note',
      render: (note) => <p className="max-w-md whitespace-pre-wrap">{note.text}</p>,
    },
    { key: 'classification', header: 'Classification', render: (note) => humanizeCode(note.classification) },
    { key: 'created_at', header: 'Created', render: (note) => formatDateTime(note.created_at, timeZone) },
  ]
}

function NotesList() {
  const factory = useFactory()
  const [classification, setClassification] = useState('')
  const [offset, setOffset] = useState(0)

  const query = useQuery({
    queryKey: ['notes', factory.id, classification, offset],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/notes', {
          params: {
            path: { factory_id: factory.id },
            query: { classification: classification || undefined, limit: NOTES_PAGE_SIZE, offset },
          },
        }),
      ),
  })

  return (
    <section aria-labelledby="notes-recent" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="notes-recent" className="text-base font-semibold">
          Recent notes
        </h2>
        <FormField id="notes-filter" label="Classification">
          <select
            id="notes-filter"
            className="input h-8 w-auto py-0"
            value={classification}
            onChange={(event) => {
              setClassification(event.target.value)
              setOffset(0)
            }}
          >
            <option value="">All</option>
            {CLASSIFICATIONS.map((value) => (
              <option key={value} value={value}>
                {humanizeCode(value)}
              </option>
            ))}
          </select>
        </FormField>
      </div>
      {query.isPending && <p className="text-fg-muted">Loading notes…</p>}
      {query.isError &&
        (query.error instanceof ApiError && query.error.status === 403 ? (
          <PermissionDenied />
        ) : (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ))}
      {query.isSuccess && query.data.items.length === 0 && (
        <EmptyState icon="chat-text" title="No notes yet" description="Notes added by your team will appear here." />
      )}
      {query.isSuccess && query.data.items.length > 0 && (
        <>
          <DataTable
            caption="Recent notes"
            columns={columns(factory.timezone)}
            rows={query.data.items}
            rowKey={(note) => note.id}
          />
          <Pagination
            total={query.data.total}
            limit={query.data.limit}
            offset={query.data.offset}
            onOffsetChange={setOffset}
          />
        </>
      )}
    </section>
  )
}

function NotesWorkspace() {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const [lastNote, setLastNote] = useState<NoteOut | null>(null)
  const factoryPath = `/f/${encodeURIComponent(factory.code)}`

  return (
    <div className="flex flex-col gap-6">
      <NoteForm
        onCreated={(note) => {
          setLastNote(note)
          void queryClient.invalidateQueries({ queryKey: ['notes', factory.id] })
        }}
      />
      {lastNote && <NoteResult note={lastNote} factoryPath={factoryPath} />}
      <NotesList />
    </div>
  )
}

export function NotesPage() {
  const can = useCan()
  return (
    <>
      <h1 className="mb-6 text-xl font-semibold tracking-tight">Notes</h1>
      {can('note:create') ? (
        <NotesWorkspace />
      ) : (
        <PermissionDenied message="Your role does not include adding or viewing notes." />
      )}
    </>
  )
}
