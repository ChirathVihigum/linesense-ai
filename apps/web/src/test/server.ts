/**
 * MSW request handlers and fixtures for component tests. These are test
 * fixtures only; nothing here is shown to users.
 */
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

import type { Schemas } from '../lib/api'

export const FACTORY_ID = '11111111-1111-4111-8111-111111111111'
export const OTHER_FACTORY_ID = '22222222-2222-4222-8222-222222222222'
export const CUSTOMER_ID = '33333333-3333-4333-8333-333333333333'
export const STYLE_ID = '44444444-4444-4444-8444-444444444444'
export const TRACE_ID = '0b7c6f1e-2d7a-4c1b-9a55-6f0e9d3c2b1a'

const READ_PERMISSIONS = [
  'analysis:read',
  'capacity:read',
  'document:read',
  'ie:read',
  'inventory:read',
  'order:read',
  'quality:read',
]
const PLANNER_PERMISSIONS = [
  ...READ_PERMISSIONS,
  'analysis:run',
  'note:create',
  'order:create',
  'order:import',
  'order:transition',
]

export function makeMe(roles: string[], permissions: string[]): Schemas['MeResponse'] {
  return {
    user: { id: '55555555-5555-4555-8555-555555555555', email: 'pat@example.test', display_name: 'Pat Planner' },
    organization: { id: '66666666-6666-4666-8666-666666666666', name: 'Demo Apparel' },
    factories: [
      { id: FACTORY_ID, code: 'F1', name: 'Factory One', timezone: 'Asia/Colombo', roles, permissions },
      {
        id: OTHER_FACTORY_ID,
        code: 'F2',
        name: 'Factory Two',
        timezone: 'Asia/Colombo',
        roles: ['viewer'],
        permissions: READ_PERMISSIONS,
      },
    ],
    csrf_token: 'csrf-test-token',
  }
}

export const plannerMe = makeMe(['planner'], PLANNER_PERMISSIONS)
export const viewerMe = makeMe(['viewer'], READ_PERMISSIONS)
export const storekeeperMe = makeMe(['storekeeper'], [...READ_PERMISSIONS, 'inventory:write', 'note:create'])
export const ieEngineerMe = makeMe(['ie_engineer'], [...READ_PERMISSIONS, 'ie:write', 'note:create'])
export const qualityManagerMe = makeMe(
  ['quality_manager'],
  [...READ_PERMISSIONS, 'quality:inspect', 'quality:hold', 'quality:release', 'note:create'],
)

export function makeOrder(overrides: Partial<Schemas['OrderSummary']> = {}): Schemas['OrderSummary'] {
  return {
    id: '77777777-7777-4777-8777-777777777777',
    external_ref: 'PO-1001',
    customer: { id: CUSTOMER_ID, code: 'CUST-001', name: 'Northwind Apparel' },
    style: { id: STYLE_ID, code: 'STY-001', name: 'Crew-neck tee' },
    quantity: 1200,
    produced_units: 300,
    packed_units: 100,
    due_date: '2026-10-15',
    priority: 2,
    production_state: 'IN_PRODUCTION',
    material_state: 'AT_RISK',
    quality_state: 'PENDING',
    shipment: { eligible: false, reasons: ['Production is not complete.'] },
    version: 3,
    updated_at: '2026-09-18T04:30:00Z',
    ...overrides,
  }
}

export function makeOrderDetail(overrides: Partial<Schemas['OrderDetail']> = {}): Schemas['OrderDetail'] {
  return {
    ...makeOrder(),
    factory: { id: FACTORY_ID, code: 'F1', name: 'Factory One' },
    bom: { version_no: 1, lines: [] },
    operations: [],
    allocations: [],
    reservations: [],
    inspections: [],
    holds: [],
    latest_run: null,
    latest_report: null,
    allowed_transitions: [],
    ...overrides,
  }
}

export function page<T>(items: T[], total = items.length, limit = 50, offset = 0) {
  return { items, total, limit, offset }
}

export function makeDashboard(overrides: Partial<Schemas['DashboardOut']> = {}): Schemas['DashboardOut'] {
  return {
    generated_at: '2026-09-20T04:30:00Z',
    status_source: 'Calculated from records',
    as_of: '2026-09-20',
    orders_at_risk: [],
    material_shortages: [],
    quality_holds: [],
    active_runs: [],
    pending_approvals: 0,
    capacity_next_7_days: [],
    ...overrides,
  }
}

export function errorBody(
  code: string,
  message: string,
  fieldErrors: { field: string; message: string }[] = [],
) {
  return {
    error: { code, message, field_errors: fieldErrors, trace_id: TRACE_ID, retry_after_seconds: null },
  }
}

export const handlers = [
  http.get('/api/v1/me', () => HttpResponse.json(plannerMe)),
  http.get('/api/v1/factories/:factoryId/orders', () => HttpResponse.json(page([makeOrder()]))),
  http.get('/api/v1/factories/:factoryId/customers', () =>
    HttpResponse.json(page([{ id: CUSTOMER_ID, code: 'CUST-001', name: 'Northwind Apparel' }])),
  ),
  http.get('/api/v1/factories/:factoryId/styles', () =>
    HttpResponse.json(
      page([{ id: STYLE_ID, code: 'STY-001', name: 'Crew-neck tee', product_type: 'T-shirt' }]),
    ),
  ),
  http.get('/api/v1/orders/:orderId', () => HttpResponse.json(makeOrderDetail())),
  http.get('/api/v1/orders/:orderId/runs', () => HttpResponse.json(page([]))),
  http.get('/api/v1/orders/:orderId/history', () => HttpResponse.json(page([]))),
  http.get('/api/v1/factories/:factoryId/recommendations', () => HttpResponse.json(page([]))),
  // The Layout header's notifications menu and the overview page's dashboard
  // fetch on every authenticated screen, so every test needs a default.
  http.get('/api/v1/factories/:factoryId/notifications', () => HttpResponse.json(page([]))),
  http.get('/api/v1/factories/:factoryId/dashboard', () => HttpResponse.json(makeDashboard())),
]

export const server = setupServer(...handlers)
