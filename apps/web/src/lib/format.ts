/**
 * Display formatting for values returned by the API. Every number and date shown
 * in the UI goes through these helpers so the presentation is consistent.
 */
export const LOCALE = 'en-GB'

/** Shown when the API returned no value (never a made-up number). */
export const MISSING_VALUE = 'Unknown'

const integerFormat = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 })

export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING_VALUE
  return integerFormat.format(value)
}

/** Decimals arrive from the API as strings (exact `Decimal` values) or numbers. */
export function formatDecimal(
  value: number | string | null | undefined,
  maximumFractionDigits = 2,
): string {
  if (value === null || value === undefined || value === '') return MISSING_VALUE
  const numeric = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(numeric)) return MISSING_VALUE
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits }).format(numeric)
}

export function formatPercent(fraction: number | null | undefined, maximumFractionDigits = 0) {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return MISSING_VALUE
  return new Intl.NumberFormat(LOCALE, { style: 'percent', maximumFractionDigits }).format(
    fraction,
  )
}

/**
 * A calendar date (`YYYY-MM-DD`, e.g. an order's due date). It is a date in the
 * factory's calendar, not an instant, so it is formatted without any time-zone
 * shift.
 */
export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return MISSING_VALUE
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate)
  if (!match) return isoDate
  const [, year, month, day] = match
  const utc = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)))
  return new Intl.DateTimeFormat(LOCALE, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(utc)
}

/** An instant (ISO 8601 with offset) shown in the factory's time zone. */
export function formatDateTime(isoDateTime: string | null | undefined, timeZone: string): string {
  if (!isoDateTime) return MISSING_VALUE
  const date = new Date(isoDateTime)
  if (Number.isNaN(date.getTime())) return isoDateTime
  return new Intl.DateTimeFormat(LOCALE, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone,
    timeZoneName: 'short',
  }).format(date)
}

/** Converts a string-backed human label: `IN_PRODUCTION` → `In production`. */
export function humanizeCode(code: string): string {
  const words = code.toLowerCase().split('_').filter(Boolean).join(' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}
