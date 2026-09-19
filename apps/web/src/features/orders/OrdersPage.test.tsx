import { screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse, delay } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { TRACE_ID, errorBody, makeOrder, page, server, viewerMe } from '../../test/server'

describe('OrdersPage', () => {
  it('shows a loading state, then order rows with separate state badges and shipment eligibility', async () => {
    server.use(
      http.get('/api/v1/factories/:factoryId/orders', async () => {
        await delay(50)
        return HttpResponse.json(
          page([
            makeOrder(),
            makeOrder({
              id: '88888888-8888-4888-8888-888888888888',
              external_ref: 'PO-1002',
              production_state: 'PRODUCTION_COMPLETE',
              material_state: 'READY',
              quality_state: 'RELEASED',
              shipment: { eligible: true, reasons: [] },
            }),
          ]),
        )
      }),
    )
    renderRoute('/f/F1/orders')

    expect(await screen.findByText('Loading orders…')).toBeInTheDocument()
    const table = await screen.findByRole('table', { name: 'Orders' })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(3)

    const first = rows[1]
    if (!first) throw new Error('missing row')
    expect(within(first).getByText('PO-1001')).toBeInTheDocument()
    expect(within(first).getByText('1,200')).toBeInTheDocument()
    expect(within(first).getByText('15 Oct 2026')).toBeInTheDocument()
    expect(within(first).getByText('In production')).toBeInTheDocument()
    expect(within(first).getByText('At risk')).toBeInTheDocument()
    expect(within(first).getByText('Pending')).toBeInTheDocument()
    expect(within(first).getByText('Not eligible')).toBeInTheDocument()
    expect(within(first).getByText('Production is not complete.')).toBeInTheDocument()

    const second = rows[2]
    if (!second) throw new Error('missing row')
    expect(within(second).getByText('Eligible')).toBeInTheDocument()

    // The server orders by due date; the header says so.
    expect(screen.getByRole('columnheader', { name: /Due date/ })).toHaveAttribute('aria-sort', 'ascending')
  })

  it('shows an empty state when the factory has no orders', async () => {
    server.use(http.get('/api/v1/factories/:factoryId/orders', () => HttpResponse.json(page([]))))
    renderRoute('/f/F1/orders')
    expect(await screen.findByText('No orders yet')).toBeInTheDocument()
  })

  it('shows the error with its trace id and retries on request', async () => {
    let calls = 0
    server.use(
      http.get('/api/v1/factories/:factoryId/orders', () => {
        calls += 1
        if (calls === 1) {
          return HttpResponse.json(errorBody('INTERNAL_ERROR', 'Unexpected server error.'), { status: 500 })
        }
        return HttpResponse.json(page([makeOrder()]))
      }),
    )
    const { user } = renderRoute('/f/F1/orders')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Unexpected server error.')
    expect(alert).toHaveTextContent(TRACE_ID)

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('PO-1001')).toBeInTheDocument()
    expect(calls).toBe(2)
  })

  it('shows a permission-denied state on 403', async () => {
    server.use(
      http.get('/api/v1/factories/:factoryId/orders', () =>
        HttpResponse.json(errorBody('FORBIDDEN', 'Missing permission order:read.'), { status: 403 }),
      ),
    )
    renderRoute('/f/F1/orders')
    expect(await screen.findByText('Permission required')).toBeInTheDocument()
  })

  it('debounces search and sends filters and pagination to the server', async () => {
    const queries: URLSearchParams[] = []
    server.use(
      http.get('/api/v1/factories/:factoryId/orders', ({ request }) => {
        const params = new URL(request.url).searchParams
        queries.push(params)
        return HttpResponse.json(page([makeOrder()], 120, 50, Number(params.get('offset') ?? 0)))
      }),
    )
    const { user } = renderRoute('/f/F1/orders')
    await screen.findByText('PO-1001')
    expect(queries).toHaveLength(1)

    await user.type(screen.getByRole('searchbox', { name: 'Search orders' }), 'PO-7')
    await waitFor(() => {
      expect(queries.at(-1)?.get('q')).toBe('PO-7')
    })
    // One request for the whole typed term, not one per keystroke.
    expect(queries.filter((q) => q.has('q'))).toHaveLength(1)

    await user.selectOptions(screen.getByLabelText('Material'), 'SHORTAGE')
    await waitFor(() => {
      expect(queries.at(-1)?.get('material_state')).toBe('SHORTAGE')
    })

    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => {
      expect(queries.at(-1)?.get('offset')).toBe('50')
    })
    expect(queries.at(-1)?.get('q')).toBe('PO-7')
  })

  it('offers New order and Import actions to planners', async () => {
    renderRoute('/f/F1/orders')
    await screen.findByText('PO-1001')
    const main = screen.getByRole('main')
    expect(within(main).getByRole('link', { name: 'New order' })).toHaveAttribute('href', '/f/F1/orders/new')
    expect(within(main).getByRole('link', { name: 'Import orders' })).toHaveAttribute(
      'href',
      '/f/F1/orders/import',
    )
  })

  it('hides New order and Import from viewers (page and navigation)', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(viewerMe)))
    renderRoute('/f/F1/orders')
    await screen.findByText('PO-1001')
    expect(screen.queryByRole('link', { name: 'New order' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Import orders' })).not.toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'All orders' })).toBeInTheDocument()
  })
})
