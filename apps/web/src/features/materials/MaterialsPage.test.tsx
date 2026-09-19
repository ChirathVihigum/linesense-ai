import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { plannerMe, server, storekeeperMe } from '../../test/server'

const MATERIAL_KNOWN = {
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

const MATERIAL_UNKNOWN_COVERAGE = {
  ...MATERIAL_KNOWN,
  material_id: 'm2',
  material_code: 'THR-002',
  material_name: 'Sewing thread',
  average_daily_consumption: '0.0000',
  coverage_days: null,
  below_reorder_point: true,
}

function mockMaterials() {
  server.use(
    http.get('/api/v1/factories/:factoryId/materials', () =>
      HttpResponse.json({ as_of: '2026-09-20', items: [MATERIAL_KNOWN, MATERIAL_UNKNOWN_COVERAGE] }),
    ),
    http.get('/api/v1/factories/:factoryId/reservations', () =>
      HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
    ),
  )
}

describe('MaterialsPage', () => {
  it('renders "Unknown" coverage when the server has no computable value, and a number otherwise', async () => {
    mockMaterials()
    renderRoute('/f/F1/materials')
    expect(await screen.findByText('16')).toBeInTheDocument()
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(screen.getByText('Below reorder point')).toBeInTheDocument()
    expect(screen.getByText('Above reorder point')).toBeInTheDocument()
  })

  it('hides the stock movement forms for a planner (no inventory:write)', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(plannerMe)))
    mockMaterials()
    renderRoute('/f/F1/materials')
    await screen.findByText('Cotton fabric')
    expect(screen.queryByText('Record stock movement')).not.toBeInTheDocument()
  })

  it('shows the stock movement forms for a storekeeper', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(storekeeperMe)))
    mockMaterials()
    renderRoute('/f/F1/materials')
    expect(await screen.findByText('Record stock movement')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Receipt' })).toBeInTheDocument()
  })
})
