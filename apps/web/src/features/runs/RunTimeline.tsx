import clsx from 'clsx'
import { Fragment } from 'react'

import { Icon, type IconName } from '../../components/Icon'
import type { Schemas } from '../../lib/api'
import { formatDateTime, humanizeCode } from '../../lib/format'
import { MessageViewer } from './MessageViewer'

type RunEvent = Schemas['RunEventOut']

const EVENT_ICON: Record<string, IconName> = {
  'run.created': 'info',
  'run.started': 'play',
  'task.dispatched': 'send',
  'task.completed': 'check-double',
  'orchestrator.replan': 'refresh',
  'run.finalized': 'flag',
  'run.cancelled': 'ban',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function TaskCompletedBody({ payload }: { payload: Record<string, unknown> }) {
  const status = typeof payload.status === 'string' ? payload.status : 'unknown'
  const summary = typeof payload.summary === 'string' ? payload.summary : null
  const findingCodes = Array.isArray(payload.finding_codes) ? payload.finding_codes : []
  return (
    <div className="text-sm">
      <p>
        Replied <span className="font-medium">{humanizeCode(status)}</span>
        {summary && `: ${summary}`}
      </p>
      {findingCodes.length > 0 && (
        <p className="text-xs text-fg-muted">Findings: {findingCodes.join(', ')}</p>
      )}
      {typeof payload.error_code === 'string' && (
        <p className="text-xs text-bad-fg">Error: {payload.error_code}</p>
      )}
    </div>
  )
}

function GenericBody({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload)
  if (entries.length === 0) return null
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-xs text-fg-muted">
      {entries.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="font-medium">{humanizeCode(key)}</dt>
          <dd className="text-fg">
            {typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
              ? String(value)
              : JSON.stringify(value)}
          </dd>
        </Fragment>
      ))}
    </dl>
  )
}

function EventBody({ event }: { event: RunEvent }) {
  const payload = isRecord(event.payload) ? event.payload : {}
  if (event.event_type === 'task.dispatched') {
    return <MessageViewer envelope={payload.message} />
  }
  if (event.event_type === 'task.completed') {
    return <TaskCompletedBody payload={payload} />
  }
  return <GenericBody payload={payload} />
}

/** The run's timeline, built from `GET /runs/{id}/events`. `orchestrator.replan`
 * is highlighted (it explains why a second planning round happened). */
export function RunTimeline({ events, timeZone }: { events: RunEvent[]; timeZone: string }) {
  if (events.length === 0) {
    return <p className="text-fg-muted">No events yet.</p>
  }
  return (
    <ol className="flex flex-col gap-3">
      {events.map((event) => {
        const isReplan = event.event_type === 'orchestrator.replan'
        const reason = isReplan && isRecord(event.payload) ? event.payload.reason : null
        return (
          <li
            key={event.id}
            className={clsx(
              'panel flex flex-col gap-1.5 p-3',
              isReplan && 'border-warn-line bg-warn-bg text-warn-fg',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-sm font-medium">
                <Icon name={EVENT_ICON[event.event_type] ?? 'info'} className="h-3.5 w-3.5" />
                {isReplan ? 'Replanning' : humanizeCode(event.event_type.replace('.', ' '))}
              </span>
              <span className="text-xs text-fg-muted">{formatDateTime(event.created_at, timeZone)}</span>
            </div>
            <p className="text-xs text-fg-muted">{event.actor}</p>
            {isReplan ? (
              <p className="text-sm">Reason: {typeof reason === 'string' ? reason : 'Unknown'}</p>
            ) : (
              <EventBody event={event} />
            )}
          </li>
        )
      })}
    </ol>
  )
}
