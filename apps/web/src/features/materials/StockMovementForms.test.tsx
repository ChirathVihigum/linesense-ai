import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { server, storekeeperMe } from '../../test/server'

const MATERIAL = {
  material_id: 'm1',
  material_code: 'FAB-001',
  material_name: 'Cotton fabric',
  unit: 'm',
  on_hand: '1000.0000',
  reserved: '200.0000',
  available_now: '800.0000',
  open_receipt_quantity: '0.0000',
  next_receipt_date: null,
  average_daily_consumption: '50.0000',
  coverage_days: '16.00',
  reorder_point: '700.0000',
  below_reorder_point: false,
  balance_version: 3,
  status_source: 'Calculated from records',
}

function mockBaseRoutes() {
  server.use(
    http.get('/api/v1/me', () => HttpResponse.json(storekeeperMe)),
    http.get('/api/v1/factories/:factoryId/materials', () =>
      HttpResponse.json({ as_of: '2026-09-20', items: [MATERIAL] }),
    ),
    http.get('/api/v1/factories/:factoryId/reservations', () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
  )
}

describe('StockMovementForms', () => {
  it('sends an Idempotency-Key header when recording a receipt', async () => {
    mockBaseRoutes()
    let idempotencyKey: string | null = null
    server.use(
      http.post('/api/v1/factories/:factoryId/stock/receipts', ({ request }) => {
        idempotencyKey = request.headers.get('Idempotency-Key')
        return HttpResponse.json(
          {
            movement: {
              id: 'mv1',
              material_id: 'm1',
              lot_id: 'lot1',
              lot_code: 'LOT-1',
              movement_type: 'RECEIPT',
              quantity: '100.0000',
              corrects_movement_id: null,
              reason: null,
              order_id: null,
              created_by: null,
              created_at: '2026-09-20T00:00:00Z',
            },
            lot: { id: 'lot1', lot_code: 'LOT-1', material_id: 'm1', status: 'ACCEPTED', received_at: '2026-09-20T00:00:00Z' },
            balance: { material_id: 'm1', on_hand_accepted: '1100.0000', reserved: '200.0000', available_now: '900.0000', version: 4 },
          },
          { status: 201 },
        )
      }),
    )
    const { user } = renderRoute('/f/F1/materials')
    await screen.findByText('Record stock movement')

    await user.selectOptions(await screen.findByLabelText(/^Material/), 'm1')
    await user.type(screen.getByLabelText(/Lot code/), 'LOT-1')
    await user.type(screen.getByLabelText(/^Quantity/), '100')
    await user.click(screen.getByRole('button', { name: 'Record receipt' }))

    await waitFor(() => {
      expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/)
    })
  })

  it('shows a 409 conflict inline without changing the material table', async () => {
    mockBaseRoutes()
    server.use(
      http.post('/api/v1/factories/:factoryId/stock/receipts', () =>
        HttpResponse.json(
          {
            error: {
              code: 'CONFLICT',
              message: 'This lot code belongs to a different material.',
              field_errors: [],
              trace_id: 'trace-1',
              retry_after_seconds: null,
            },
          },
          { status: 409 },
        ),
      ),
    )
    const { user } = renderRoute('/f/F1/materials')
    await screen.findByText('Record stock movement')

    await user.selectOptions(await screen.findByLabelText(/^Material/), 'm1')
    await user.type(screen.getByLabelText(/Lot code/), 'LOT-1')
    await user.type(screen.getByLabelText(/^Quantity/), '100')
    await user.click(screen.getByRole('button', { name: 'Record receipt' }))

    expect(await screen.findByText('This lot code belongs to a different material.')).toBeInTheDocument()
    // The overview table is unchanged: no optimistic update happened.
    expect(screen.getByText('800')).toBeInTheDocument()
  })

  it('marks the required receipt fields with aria-required', async () => {
    mockBaseRoutes()
    renderRoute('/f/F1/materials')
    await screen.findByText('Record stock movement')
    expect(screen.getByLabelText(/^Material/)).toHaveAttribute('aria-required', 'true')
    expect(screen.getByLabelText(/Lot code/)).toHaveAttribute('aria-required', 'true')
    expect(screen.getByLabelText(/^Quantity/)).toHaveAttribute('aria-required', 'true')
  })
})
