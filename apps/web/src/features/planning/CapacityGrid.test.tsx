import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router'

import type { Schemas } from '../../lib/api'
import { CapacityGrid } from './CapacityGrid'

const LINE_ID = 'aaaaaaaa-0000-4000-8000-000000000001'
const ORDER_ID = 'bbbbbbbb-0000-4000-8000-000000000002'

function makeBoard(slots: Partial<Schemas['SlotOut']>[]): Schemas['CapacityBoardOut'] {
  return {
    factory_id: 'cccccccc-0000-4000-8000-000000000003',
    start: '2026-10-01',
    end: '2026-10-01',
    lines: [
      {
        id: LINE_ID,
        code: 'L1',
        name: 'Line 1',
        operator_count: 20,
        is_active: true,
        slots: slots.map((overrides) => ({
          id: 'dddddddd-0000-4000-8000-000000000004',
          slot_date: '2026-10-01',
          shift_code: 'A',
          available_operator_minutes: '9600',
          planned_efficiency: '0.85',
          capacity_standard_minutes: '8160',
          allocated_standard_minutes: '4000',
          remaining_standard_minutes: '4160',
          utilization: '0.49',
          version: 1,
          allocations: [],
          ...overrides,
        })),
      },
    ],
  }
}

function renderGrid(board: Schemas['CapacityBoardOut']) {
  return render(
    <MemoryRouter>
      <CapacityGrid board={board} factoryCode="F1" />
    </MemoryRouter>,
  )
}

describe('CapacityGrid', () => {
  it('renders the utilization percentage as text for every slot', () => {
    renderGrid(makeBoard([{ utilization: '0.49' }]))
    expect(screen.getByText('49%')).toBeInTheDocument()
  })

  it('flags a slot at or above 95% utilization with an icon and text', () => {
    renderGrid(makeBoard([{ utilization: '0.97' }]))
    expect(screen.getByText('97%')).toBeInTheDocument()
    expect(screen.getByText('High utilization')).toBeInTheDocument()
  })

  it('does not flag a slot below 95% utilization', () => {
    renderGrid(makeBoard([{ utilization: '0.94' }]))
    expect(screen.getByText('94%')).toBeInTheDocument()
    expect(screen.queryByText('High utilization')).not.toBeInTheDocument()
  })

  it('shows unknown utilization without a percentage or a flag', () => {
    renderGrid(makeBoard([{ utilization: null }]))
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(screen.queryByText('High utilization')).not.toBeInTheDocument()
  })

  it('renders an allocation chip linking to the order in the orders list', () => {
    renderGrid(
      makeBoard([
        {
          allocations: [
            { id: 'eeeeeeee-0000-4000-8000-000000000005', order_id: ORDER_ID, order_external_ref: 'PO-9001', standard_minutes: '120', units: '60' },
          ],
        },
      ]),
    )
    const chip = screen.getByRole('link', { name: 'PO-9001' })
    expect(chip).toHaveAttribute('href', '/f/F1/orders?q=PO-9001')
  })

  it('shows "No slot" for a shift with no scheduled slot', () => {
    renderGrid(makeBoard([{ shift_code: 'A' }]))
    expect(screen.getAllByText('No slot').length).toBeGreaterThan(0)
  })
})
