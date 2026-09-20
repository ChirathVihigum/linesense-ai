import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { makeMe, page, server, viewerMe } from '../../test/server'

describe('AdminPage', () => {
  it('denies access to a non-admin', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(viewerMe)))
    renderRoute('/f/F1/admin')
    expect(await screen.findByText('Permission required')).toBeInTheDocument()
  })

  it('shows the four tabs to an org_admin', async () => {
    const adminMe = makeMe(['org_admin'], ['order:read', 'admin:manage', 'audit:read'])
    server.use(
      http.get('/api/v1/me', () => HttpResponse.json(adminMe)),
      http.get('/api/v1/admin/memberships', () => HttpResponse.json({ items: [], total: 0, limit: 200, offset: 0 })),
      http.get('/api/v1/factories/:factoryId/audit-events', () => HttpResponse.json(page([]))),
    )
    renderRoute('/f/F1/admin')
    expect(await screen.findByRole('tab', { name: 'Audit log' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Memberships' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Quality policies' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Settings' })).toBeInTheDocument()
  })
})
