/**
 * ARIA props linking a control to its FormField hint and error. Pass
 * `required` for any field marked required in `FormField` so the control
 * carries both `aria-required` and the native `required` attribute (screen
 * readers and browser form validation agree with the visible "*").
 */
export function fieldAria(id: string, error?: string, hint?: string, required?: boolean) {
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null]
    .filter(Boolean)
    .join(' ')
  return {
    id,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': describedBy || undefined,
    'aria-required': required ? true : undefined,
    required: required ? true : undefined,
  } as const
}
