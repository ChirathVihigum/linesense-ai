/**
 * Typed HTTP client for the LineSense backend.
 *
 * - Types come from `src/generated/api.ts` (generated from `contracts/openapi.json`).
 * - The session is an HttpOnly cookie (`ls_session`); requests are same-origin so the
 *   browser sends it. No token is ever stored in JavaScript-accessible storage.
 * - Unsafe methods carry `X-CSRF-Token` (the value from `GET /api/v1/me`, held in memory).
 * - A 401 from any route sends the browser to `/login?next=<current path>`.
 * - `Idempotency-Key` is passed per call as a typed header parameter (see `idempotency.ts`).
 */
import createClient, { type Client, type Middleware } from 'openapi-fetch'

import type { components, paths } from '../generated/api'

export type Schemas = components['schemas']

export const CSRF_HEADER = 'X-CSRF-Token'
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

/** In-memory session state shared between the auth provider and the client. */
export const sessionState: { csrfToken: string | null } = { csrfToken: null }

export function setCsrfToken(token: string | null): void {
  sessionState.csrfToken = token
}

type BrowserLocation = Pick<Location, 'pathname' | 'search' | 'hash' | 'assign'>

/** Full-page navigation (leaves the SPA, e.g. after sign-out). Replaceable in tests. */
export const browserNavigation = {
  assign(url: string): void {
    window.location.assign(url)
  },
}

export function loginUrl(next: string): string {
  return `/login?next=${encodeURIComponent(next)}`
}

/** Sends the browser to `/login?next=<current path>` (no-op when already there). */
export function redirectToLogin(location: BrowserLocation = window.location): void {
  if (location.pathname === '/login') return
  location.assign(loginUrl(`${location.pathname}${location.search}${location.hash}`))
}

export interface ApiClientOptions {
  baseUrl?: string
  getCsrfToken?: () => string | null
  onUnauthenticated?: () => void
  fetch?: typeof globalThis.fetch
}

export function createSessionMiddleware(
  getCsrfToken: () => string | null,
  onUnauthenticated: () => void,
): Middleware {
  return {
    onRequest({ request }) {
      if (!SAFE_METHODS.has(request.method.toUpperCase())) {
        const token = getCsrfToken()
        if (token) request.headers.set(CSRF_HEADER, token)
      }
      return request
    },
    onResponse({ response }) {
      if (response.status === 401) onUnauthenticated()
      return response
    },
  }
}

export function createApiClient(options: ApiClientOptions = {}): Client<paths> {
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? window.location.origin,
    credentials: 'same-origin',
    // Resolve fetch per request (not at import time) so instrumentation such as
    // test request interception installed later is honoured.
    fetch: options.fetch ?? ((request: Request) => globalThis.fetch(request)),
  })
  client.use(
    createSessionMiddleware(
      options.getCsrfToken ?? (() => sessionState.csrfToken),
      options.onUnauthenticated ??
        (() => {
          redirectToLogin()
        }),
    ),
  )
  return client
}

export const api = createApiClient()

export interface FieldError {
  field: string
  message: string
}

/** A non-2xx response, parsed from the contract error body. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fieldErrors: FieldError[]
  readonly traceId: string | null
  readonly retryAfterSeconds: number | null

  constructor(init: {
    status: number
    code: string
    message: string
    fieldErrors?: FieldError[]
    traceId?: string | null
    retryAfterSeconds?: number | null
  }) {
    super(init.message)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.fieldErrors = init.fieldErrors ?? []
    this.traceId = init.traceId ?? null
    this.retryAfterSeconds = init.retryAfterSeconds ?? null
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseFieldErrors(value: unknown): FieldError[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item: unknown) =>
    isRecord(item) && typeof item.field === 'string' && typeof item.message === 'string'
      ? [{ field: item.field, message: item.message }]
      : [],
  )
}

export function parseApiError(response: Response, body: unknown): ApiError {
  const headerTraceId = response.headers.get('X-Request-Id')
  const error = isRecord(body) && isRecord(body.error) ? body.error : null
  if (error === null) {
    return new ApiError({
      status: response.status,
      code: response.status >= 500 ? 'INTERNAL_ERROR' : 'UNKNOWN_ERROR',
      message: `The server responded with status ${response.status}.`,
      traceId: headerTraceId,
    })
  }
  return new ApiError({
    status: response.status,
    code: typeof error.code === 'string' ? error.code : 'UNKNOWN_ERROR',
    message:
      typeof error.message === 'string'
        ? error.message
        : `The server responded with status ${response.status}.`,
    fieldErrors: parseFieldErrors(error.field_errors),
    traceId: typeof error.trace_id === 'string' ? error.trace_id : headerTraceId,
    retryAfterSeconds:
      typeof error.retry_after_seconds === 'number' ? error.retry_after_seconds : null,
  })
}

/** Converts anything thrown by a request (network failure included) to an `ApiError`. */
export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError({
    status: 0,
    code: 'NETWORK_ERROR',
    message: 'Could not reach the LineSense server. Check your connection and try again.',
  })
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response }

/** Resolves to `data` on 2xx, otherwise throws an `ApiError`. */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>
  try {
    result = await request
  } catch (error) {
    throw toApiError(error)
  }
  if (!result.response.ok) throw parseApiError(result.response, result.error)
  return result.data as T
}

/** Fetches every page of a paginated collection (`limit` ≤ 200 per the contract). */
export async function fetchAllPages<T>(
  fetchPage: (offset: number, limit: number) => Promise<{ items: T[]; total: number }>,
  limit = 200,
): Promise<T[]> {
  const items: T[] = []
  for (;;) {
    const page = await fetchPage(items.length, limit)
    items.push(...page.items)
    if (page.items.length === 0 || items.length >= page.total) return items
  }
}
