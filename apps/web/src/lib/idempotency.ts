import { useCallback, useRef } from 'react'

/** A fresh `Idempotency-Key` (8..128 chars per the contract; a UUID is 36). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}

/**
 * Holds one `Idempotency-Key` per submission attempt.
 *
 * `keyFor(payload)` returns the same key while the payload is unchanged, so a
 * retry of the same attempt (after a network error or a 5xx) is de-duplicated by
 * the server. A changed payload is a new attempt and gets a new key (reusing a
 * key for different content would be rejected with `IDEMPOTENCY_KEY_REUSED`).
 * Call `reset()` after a successful submission.
 */
export function useIdempotencyKey(): {
  keyFor: (payload: unknown) => string
  reset: () => void
} {
  const attempt = useRef<{ key: string; fingerprint: string } | null>(null)

  const keyFor = useCallback((payload: unknown) => {
    const fingerprint = JSON.stringify(payload)
    if (attempt.current?.fingerprint !== fingerprint) {
      attempt.current = { key: newIdempotencyKey(), fingerprint }
    }
    return attempt.current.key
  }, [])

  const reset = useCallback(() => {
    attempt.current = null
  }, [])

  return { keyFor, reset }
}
