import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse, delay } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import {
  CUSTOMER_ID,
  FACTORY_ID,
  STYLE_ID,
  TRACE_ID,
  errorBody,
  makeOrder,
  server,
  viewerMe,
} from '../../test/server'

type User = ReturnType<typeof renderRoute>['user']

async function fillValidForm(user: User) {
  await user.type(await screen.findByLabelText(/External reference/), 'PO-2001')
  await screen.findByRole('option', { name: 'Northwind Apparel (CUST-001)' })
  await user.selectOptions(screen.getByLabelText(/Customer/), CUSTOMER_ID)
  await screen.findByRole('option', { name: 'Crew-neck tee (STY-001)' })
  await user.selectOptions(screen.getByLabelText(/Style/), STYLE_ID)
  await user.type(screen.getByLabelText(/Quantity/), '500')
  await user.type(screen.getByLabelText(/Due date/), '2026-12-31')
  await user.clear(screen.getByLabelText(/Priority/))
  await user.type(screen.getByLabelText(/Priority/), '2')
}

describe('OrderCreatePage', () => {
  it('shows validation errors from the Zod schema without calling the server', async () => {
    let posted = false
    server.use(
      http.post('/api/v1/factories/:factoryId/orders', () => {
        posted = true
        return HttpResponse.json(makeOrder(), { status: 201 })
      }),
    )
    const { user } = renderRoute('/f/F1/orders/new')
    await user.type(await screen.findByLabelText(/External reference/), 'po 1')
    await user.clear(screen.getByLabelText(/Priority/))
    await user.type(screen.getByLabelText(/Priority/), '9')
    await user.click(screen.getByRole('button', { name: 'Create order' }))

    expect(
      await screen.findByText('Use 3 to 40 characters: capital letters, digits and hyphens, starting with a letter or digit.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Select a customer.')).toBeInTheDocument()
    expect(screen.getByText('Select a style.')).toBeInTheDocument()
    expect(screen.getByText('Enter a whole number from 1 to 1,000,000.')).toBeInTheDocument()
    expect(screen.getByText('Enter a due date.')).toBeInTheDocument()
    expect(screen.getByText('Priority must be 1 (highest) to 5.')).toBeInTheDocument()
    expect(screen.getByLabelText(/External reference/)).toHaveAttribute('aria-invalid', 'true')
    expect(posted).toBe(false)
  })

  it('maps a 409 duplicate onto the external reference field and keeps the input', async () => {
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
    const { user } = renderRoute('/f/F1/orders/new')
    await fillValidForm(user)
    await user.click(screen.getByRole('button', { name: 'Create order' }))

    expect(await screen.findByText('Already exists.')).toBeInTheDocument()
    expect(screen.getByLabelText(/External reference/)).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText(/External reference/)).toHaveValue('PO-2001')
    expect(screen.getByLabelText(/Quantity/)).toHaveValue(500)
    expect(screen.getByRole('alert')).toHaveTextContent(TRACE_ID)
  })

  it('disables submit while pending and reuses one Idempotency-Key when retrying the same attempt', async () => {
    const keys: (string | null)[] = []
    const bodies: unknown[] = []
    server.use(
      http.post('/api/v1/factories/:factoryId/orders', async ({ request, params }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        bodies.push(await request.json())
        expect(params.factoryId).toBe(FACTORY_ID)
        expect(request.headers.get('X-CSRF-Token')).toBe('csrf-test-token')
        await delay(100)
        if (keys.length === 1) {
          return HttpResponse.json(errorBody('SERVICE_UNAVAILABLE', 'Try again shortly.'), { status: 503 })
        }
        return HttpResponse.json(makeOrder({ external_ref: 'PO-2001' }), { status: 201 })
      }),
      http.get('/api/v1/factories/:factoryId/orders', () => HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })),
    )
    const { user, router } = renderRoute('/f/F1/orders/new')
    await fillValidForm(user)

    await user.click(screen.getByRole('button', { name: 'Create order' }))
    expect(await screen.findByRole('button', { name: 'Creating…' })).toBeDisabled()
    expect(await screen.findByText('Try again shortly.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Create order' }))
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/f/F1/orders')
    })
    expect(await screen.findByText('Order PO-2001 created.')).toBeInTheDocument()

    expect(keys).toHaveLength(2)
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/)
    expect(keys[1]).toBe(keys[0])
    expect(bodies[0]).toEqual({
      external_ref: 'PO-2001',
      customer_id: CUSTOMER_ID,
      style_id: STYLE_ID,
      quantity: 500,
      due_date: '2026-12-31',
      priority: 2,
    })
  })

  it('uses a new Idempotency-Key when the input changes after a failure', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.post('/api/v1/factories/:factoryId/orders', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json(
          errorBody('CONFLICT', 'Duplicate.', [{ field: 'external_ref', message: 'Already exists.' }]),
          { status: 409 },
        )
      }),
    )
    const { user } = renderRoute('/f/F1/orders/new')
    await fillValidForm(user)
    await user.click(screen.getByRole('button', { name: 'Create order' }))
    await screen.findByText('Already exists.')
    await user.type(screen.getByLabelText(/External reference/), '-B')
    await user.click(screen.getByRole('button', { name: 'Create order' }))
    await waitFor(() => {
      expect(keys).toHaveLength(2)
    })
    expect(keys[1]).not.toBe(keys[0])
  })

  it('shows permission denied to users who cannot create orders', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(viewerMe)))
    renderRoute('/f/F1/orders/new')
    expect(await screen.findByText('Permission required')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create order' })).not.toBeInTheDocument()
  })
})
