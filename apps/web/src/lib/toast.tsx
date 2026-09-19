import { createContext, use, useCallback, useMemo, useRef, useState, type ReactNode } from 'react'

import { Icon } from '../components/Icon'

interface Toast {
  id: number
  message: string
  tone: 'success' | 'info'
}

interface ToastApi {
  showToast: (message: string, tone?: Toast['tone']) => void
}

const ToastContext = createContext<ToastApi | null>(null)
const TOAST_DURATION_MS = 6000

/** Polite live-region toasts for confirmations (e.g. "Order PO-1001 created"). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const showToast = useCallback(
    (message: string, tone: Toast['tone'] = 'success') => {
      const id = nextId.current++
      setToasts((current) => [...current, { id, message, tone }])
      window.setTimeout(() => {
        dismiss(id)
      }, TOAST_DURATION_MS)
    },
    [dismiss],
  )

  const value = useMemo(() => ({ showToast }), [showToast])

  return (
    <ToastContext value={value}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed right-4 bottom-4 z-50 flex flex-col gap-2"
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={
              toast.tone === 'success'
                ? 'pointer-events-auto flex items-center gap-3 rounded-md border border-ok-line bg-ok-bg px-4 py-3 text-ok-fg shadow-md shadow-zinc-950/10'
                : 'pointer-events-auto flex items-center gap-3 rounded-md border border-info-line bg-info-bg px-4 py-3 text-info-fg shadow-md shadow-zinc-950/10'
            }
          >
            <Icon name={toast.tone === 'success' ? 'check-circle' : 'info'} />
            <span>{toast.message}</span>
            <button
              type="button"
              className="ml-2 rounded p-0.5 hover:bg-surface-sunken"
              onClick={() => {
                dismiss(toast.id)
              }}
              aria-label="Dismiss notification"
            >
              <Icon name="x" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext>
  )
}

export function useToast(): ToastApi {
  const toastApi = use(ToastContext)
  if (toastApi === null) throw new Error('useToast() must be used inside <ToastProvider>.')
  return toastApi
}
