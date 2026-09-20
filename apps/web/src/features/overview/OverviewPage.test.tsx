import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { makeDashboard, server } from '../../test/server'

describe('OverviewPage', () => {
  it('renders every dashboard section with its data and an "as of" freshness label', async () => {
    server.use(
      http.get('/api/v1/factories/:factoryId/dashboard', () =>
        HttpResponse.json(
          makeDashboard({
            generated_at: '2026-09-20T09:15:00Z',
            orders_at_risk: [
              {
                id: 'a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1',
                external_ref: 'PO-9001',
                customer_code: 'CUST-1',
                style_code: 'STY-1',
                due_date: '2026-09-22',
                quantity: 500,
                produced_units: 0,
                priority: 1,
                production_state: 'VALIDATED',
                material_state: 'SHORTAGE',
                quality_state: 'NOT_INSPECTED',
              },
            ],
            material_shortages: [
              {
                material_id: 'b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2',
                material_code: 'FAB-1',
                material_name: 'Cotton fabric',
                unit: 'm',
                available_now: '10.0000',
                demand: '200.0000',
                reorder_point: '0.0000',
                below_reorder_point: false,
                shortage_qty: '190.0000',
              },
            ],
            quality_holds: [
              {
                id: 'c3c3c3c3-c3c3-4c3c-8c3c-c3c3c3c3c3c3',
                order_id: 'd4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d4',
                order_external_ref: 'PO-9002',
                reason: 'Defect rate above threshold',
                created_at: '2026-09-19T10:00:00Z',
              },
            ],
            active_runs: [
              {
                id: 'e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e5',
                order_id: 'd4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d4',
                order_external_ref: 'PO-9003',
                status: 'RUNNING',
                model_calls_used: 2,
                model_calls_limit: 12,
                started_at: '2026-09-20T09:00:00Z',
                deadline_at: '2026-09-20T09:02:00Z',
              },
            ],
            pending_approvals: 3,
            capacity_next_7_days: [
              {
                line_id: 'f6f6f6f6-f6f6-4f6f-8f6f-f6f6f6f6f6f6',
                line_code: 'L01',
                line_name: 'Line 1',
                capacity_minutes: '1080.00',
                allocated_minutes: '540.00',
                utilization_fraction: '0.5',
              },
            ],
          }),
        ),
      ),
    )

    renderRoute('/f/F1/overview')

    expect(await screen.findByText('PO-9001')).toBeInTheDocument()
    expect(screen.getByText('Cotton fabric')).toBeInTheDocument()
    expect(screen.getByText('190 short')).toBeInTheDocument()
    expect(screen.getByText('PO-9002')).toBeInTheDocument()
    expect(screen.getByText('Defect rate above threshold')).toBeInTheDocument()
    expect(screen.getByText('PO-9003')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('Line 1')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('Calculated from records')).toBeInTheDocument()
    expect(screen.getByText(/As of/)).toBeInTheDocument()
  })

  it('shows an empty state for a section with no data', async () => {
    server.use(http.get('/api/v1/factories/:factoryId/dashboard', () => HttpResponse.json(makeDashboard())))
    renderRoute('/f/F1/overview')
    expect(await screen.findByText('No orders are at risk in the next 7 days.')).toBeInTheDocument()
    expect(screen.getByText('No material shortages against open demand.')).toBeInTheDocument()
    expect(screen.getByText('No active quality holds.')).toBeInTheDocument()
    expect(screen.getByText('No analysis runs are in progress.')).toBeInTheDocument()
    expect(screen.getByText('No active lines')).toBeInTheDocument()
  })
})
