/** The board's date range is capped at 31 days (grid width and query cost). */
export const MAX_RANGE_DAYS = 31

export function isoDateInTimeZone(timeZone: string, date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(date)
    .reduce<Record<string, string>>((acc, part) => ({ ...acc, [part.type]: part.value }), {})
  return `${parts.year}-${parts.month}-${parts.day}`
}

export function addDaysIso(iso: string, days: number): string {
  const parsed = new Date(`${iso}T00:00:00Z`)
  parsed.setUTCDate(parsed.getUTCDate() + days)
  return parsed.toISOString().slice(0, 10)
}

export function daysBetweenInclusive(start: string, end: string): number {
  const startMs = new Date(`${start}T00:00:00Z`).getTime()
  const endMs = new Date(`${end}T00:00:00Z`).getTime()
  return Math.round((endMs - startMs) / 86_400_000) + 1
}

export function rangeError(start: string, end: string): string | undefined {
  if (!start || !end) return 'Choose a start and an end date.'
  if (end < start) return 'The end date must be on or after the start date.'
  const days = daysBetweenInclusive(start, end)
  if (days > MAX_RANGE_DAYS) return `Choose a range of ${MAX_RANGE_DAYS} days or fewer (currently ${days} days).`
  return undefined
}
