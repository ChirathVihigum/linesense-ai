import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { FACTORY_ID, server } from '../../test/server'
import { daysBetweenInclusive } from './dateRange'

function makeBoard() {
  return {
    factory_id: FACTORY_ID,
    start: '2026-10-01',
    end: '2026-10-01',
    lines: [
      {
        id: 'line-1',
        code: 'L1',
        name: 'Line 1',
        operator_count: 20,
        is_active: true,
        slots: [],
      },
    ],
  }
}

describe('daysBetweenInclusive', () => {
  it('counts both endpoints', () => {
    expect(daysBetweenInclusive('2026-10-01', '2026-10-01')).toBe(1)
    expect(daysBetweenInclusive('2026-10-01', '2026-10-31')).toBe(31)
  })
})

describe('PlanningBoardPage', () => {
  it('loads the capacity board for the default date range', async () => {
    let requestedQuery: URLSearchParams | undefined
    server.use(
      http.get('/api/v1/factories/:factoryId/capacity', ({ request }) => {
        requestedQuery = new URL(request.url).searchParams
        return HttpResponse.json(makeBoard())
      }),
    )
    renderRoute('/f/F1/planning')
    expect(await screen.findByText('L1')).toBeInTheDocument()
    await waitFor(() => {
      expect(requestedQuery?.get('start')).toBeTruthy()
      expect(requestedQuery?.get('end')).toBeTruthy()
    })
  })

  it('rejects a date range longer than 31 days without calling the server', async () => {
    let calls = 0
    server.use(
      http.get('/api/v1/factories/:factoryId/capacity', () => {
        calls += 1
        return HttpResponse.json(makeBoard())
      }),
    )
    const { user } = renderRoute('/f/F1/planning')
    await screen.findByText('L1')
    calls = 0
    const endInput = screen.getByLabelText(/End date/)
    await user.clear(endInput)
    await user.type(endInput, '2027-06-01')
    expect(await screen.findByText(/Choose a range of 31 days or fewer/)).toBeInTheDocument()
    expect(calls).toBe(0)
  })
})
