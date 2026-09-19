import type { ReactNode } from 'react'

export function FormField({
  id,
  label,
  error,
  hint,
  required = false,
  children,
}: {
  id: string
  label: string
  error?: string
  hint?: string
  required?: boolean
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-medium">
        {label}
        {required && (
          <span className="text-bad-fg" aria-hidden="true">
            {' '}
            *
          </span>
        )}
      </label>
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-fg-muted">
          {hint}
        </p>
      )}
      {children}
      {error && (
        <p id={`${id}-error`} className="text-xs font-medium text-bad-fg">
          {error}
        </p>
      )}
    </div>
  )
}
