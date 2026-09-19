import { useEffect, useId, useRef, type ReactNode } from 'react'

/** A modal confirmation. Focus moves to Cancel on open; Escape cancels. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  pending = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  cancelLabel?: string
  pending?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const titleId = useId()
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const previouslyFocused = document.activeElement as HTMLElement | null
    cancelRef.current?.focus()
    return () => {
      previouslyFocused?.focus()
    }
  }, [open])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="panel w-full max-w-md p-6 shadow-lg shadow-zinc-950/10"
        onKeyDown={(event) => {
          if (event.key === 'Escape' && !pending) onCancel()
        }}
      >
        <h2 id={titleId} className="text-base font-semibold">
          {title}
        </h2>
        <div className="mt-2 text-fg-muted">{children}</div>
        <div className="mt-6 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            className="btn-secondary"
            disabled={pending}
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button type="button" className="btn-primary" disabled={pending} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
