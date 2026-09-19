import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import { server, TRACE_ID, errorBody, FACTORY_ID } from '../test/server'
import { ApiError, CSRF_HEADER, createApiClient, redirectToLogin, unwrap } from './api'

function clientWithSpies() {
  const onUnauthenticated = vi.fn()
  const client = createApiClient({ getCsrfToken: () => 'token-123', onUnauthenticated })
  return { client, onUnauthenticated }
}

describe('api client middleware', () => {
  it('adds the CSRF header to unsafe methods only', async () => {
    const seen: Record<string, string | null> = {}
    server.use(
      http.get('/api/v1/me', ({ request }) => {
        seen.GET = request.headers.get(CSRF_HEADER)
        return HttpResponse.json({})
      }),
      http.post('/auth/logout', ({ request }) => {
        seen.POST = request.headers.get(CSRF_HEADER)
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const { client } = clientWithSpies()

    await client.GET('/api/v1/me')
    await client.POST('/auth/logout')

    expect(seen).toEqual({ GET: null, POST: 'token-123' })
  })

  it('sends the Idempotency-Key header when given and sends same-origin credentials', async () => {
    let received: Request | undefined
    server.use(
      http.post('/api/v1/imports/:batchId/commit', ({ request }) => {
        received = request
        return HttpResponse.json({})
      }),
    )
    const { client } = clientWithSpies()
    await client.POST('/api/v1/imports/{batch_id}/commit', {
      params: { path: { batch_id: 'b1' }, header: { 'Idempotency-Key': 'key-abcdefgh' } },
    })
    expect(received?.headers.get('Idempotency-Key')).toBe('key-abcdefgh')
    expect(received?.headers.get(CSRF_HEADER)).toBe('token-123')
    expect(received?.credentials).toBe('same-origin')
  })

  it('calls the unauthenticated handler (redirect to /login) on 401', async () => {
    server.use(
      http.get('/api/v1/me', () =>
        HttpResponse.json(errorBody('UNAUTHENTICATED', 'Sign in required.'), { status: 401 }),
      ),
    )
    const { client, onUnauthenticated } = clientWithSpies()
    await expect(unwrap(client.GET('/api/v1/me'))).rejects.toMatchObject({
      status: 401,
      code: 'UNAUTHENTICATED',
    })
    expect(onUnauthenticated).toHaveBeenCalledTimes(1)
  })

  it('does not treat other errors as unauthenticated', async () => {
    server.use(
      http.get('/api/v1/me', () =>
        HttpResponse.json(errorBody('FORBIDDEN', 'No.'), { status: 403 }),
      ),
    )
    const { client, onUnauthenticated } = clientWithSpies()
    await expect(unwrap(client.GET('/api/v1/me'))).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthenticated).not.toHaveBeenCalled()
  })

  it('redirectToLogin sends the browser to /login with the current path as next', () => {
    const assign = vi.fn()
    redirectToLogin({ pathname: '/f/F1/orders', search: '?q=PO', hash: '', assign })
    expect(assign).toHaveBeenCalledWith('/login?next=%2Ff%2FF1%2Forders%3Fq%3DPO')
  })

  it('redirectToLogin does nothing on the login page itself', () => {
    const assign = vi.fn()
    redirectToLogin({ pathname: '/login', search: '?error=auth_failed', hash: '', assign })
    expect(assign).not.toHaveBeenCalled()
  })
})

describe('ApiError parsing', () => {
  it('parses the contract error body including field errors and trace id', async () => {
    server.use(
      http.post('/api/v1/factories/:factoryId/orders', () =>
        HttpResponse.json(
          errorBody('CONFLICT', 'An order with this external reference already exists.', [
            { field: 'external_ref', message: 'Already exists.' },
          ]),
          { status: 409 },
        ),
      ),
    )
    const { client } = clientWithSpies()
    const error: unknown = await unwrap(
      client.POST('/api/v1/factories/{factory_id}/orders', {
        params: { path: { factory_id: FACTORY_ID }, header: { 'Idempotency-Key': 'key-abcdefgh' } },
        body: {
          external_ref: 'PO-1',
          customer_id: 'c',
          style_id: 's',
          quantity: 1,
          due_date: '2026-10-01',
          priority: 3,
        },
      }),
    ).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 409,
      code: 'CONFLICT',
      traceId: TRACE_ID,
      fieldErrors: [{ field: 'external_ref', message: 'Already exists.' }],
    })
  })

  it('falls back to the X-Request-Id header when the body is not a contract error', async () => {
    server.use(
      http.get('/api/v1/me', () =>
        new HttpResponse('Bad gateway', { status: 502, headers: { 'X-Request-Id': 'trace-from-header' } }),
      ),
    )
    const { client } = clientWithSpies()
    await expect(unwrap(client.GET('/api/v1/me'))).rejects.toMatchObject({
      status: 502,
      traceId: 'trace-from-header',
    })
  })
})
