/** A `task.dispatched` event's envelope: sender, recipient, task type, round,
 * input refs and constraints (backend-contracts.md §6, `TaskEnvelope`). The
 * event payload is untyped JSON (`RunEventOut.payload`), so fields are read
 * defensively rather than assumed present. */
interface EnvelopeLike {
  sender?: unknown
  recipient?: unknown
  task_type?: unknown
  round?: unknown
  input_refs?: unknown
  constraints?: unknown
  [key: string]: unknown
}

function asString(value: unknown, fallback = 'unknown'): string {
  return typeof value === 'string' ? value : fallback
}

/** The dispatched task's envelope: a visible summary line plus the full envelope as
 * collapsible, pretty-printed JSON (never `dangerouslySetInnerHTML`). */
export function MessageViewer({ envelope }: { envelope: unknown }) {
  const data = (envelope ?? {}) as EnvelopeLike
  const sender = asString(data.sender, 'orchestrator')
  const recipient = asString(data.recipient)
  const taskType = asString(data.task_type)
  const round = typeof data.round === 'number' ? data.round : null

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm">
        <span className="font-mono text-xs">{sender}</span>
        <span aria-hidden="true"> → </span>
        <span className="sr-only"> to </span>
        <span className="font-mono text-xs">{recipient}</span>
        <span> · {taskType}</span>
        {round !== null && <span> · round {round}</span>}
      </p>
      <details className="text-xs">
        <summary className="cursor-pointer font-medium text-fg-muted">View envelope</summary>
        <pre className="mt-2 max-w-full overflow-x-auto rounded-md bg-surface-sunken p-3 whitespace-pre-wrap">
          {JSON.stringify(data, null, 2)}
        </pre>
      </details>
    </div>
  )
}
