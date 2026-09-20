import { screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { FACTORY_ID, makeMe, page, server } from '../../test/server'

const adminMe = makeMe(['org_admin'], ['order:read', 'admin:manage', 'audit:read'])

const PLANNER_MEMBERSHIP_ID = '11111111-1111-4111-8111-111111111111'
const ROLE_ASSIGNMENT_ID = '22222222-2222-4222-8222-222222222222'

type RoleRow = { id: string; role: string; factory_id: string | null; factory_code: string | null }

function membership(roles: RoleRow[]) {
  return {
    id: PLANNER_MEMBERSHIP_ID,
    user_id: '99999999-9999-4999-8999-999999999999',
    email: 'planner@demo.test',
    display_name: 'Pat Planner',
    is_active: true,
    roles,
  }
}

function installHandlers(): { deleteCalls: () => number } {
  let roles: RoleRow[] = [{ id: ROLE_ASSIGNMENT_ID, role: 'planner', factory_id: FACTORY_ID, factory_code: 'F1' }]
  let deleteCalls = 0
  server.use(
    http.get('/api/v1/me', () => HttpResponse.json(adminMe)),
    http.get('/api/v1/factories/:factoryId/audit-events', () => HttpResponse.json(page([]))),
    http.get('/api/v1/admin/memberships', () => HttpResponse.json(page([membership(roles)]))),
    http.delete('/api/v1/admin/role-assignments/:id', ({ params }) => {
      deleteCalls += 1
      expect(params.id).toBe(ROLE_ASSIGNMENT_ID)
      roles = roles.filter((role) => role.id !== params.id)
      return HttpResponse.json(membership(roles))
    }),
  )
  return { deleteCalls: () => deleteCalls }
}

describe('MembershipTable', () => {
  it('revokes a role only after the confirm dialog is accepted', async () => {
    const handlers = installHandlers()
    const { user } = renderRoute('/f/F1/admin')
    await user.click(await screen.findByRole('tab', { name: 'Memberships' }))

    const revokeButton = await screen.findByRole('button', { name: 'Revoke planner at F1' })
    await user.click(revokeButton)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Revoke Planner?')).toBeInTheDocument()
    expect(handlers.deleteCalls()).toBe(0)

    await user.click(within(dialog).getByRole('button', { name: 'Revoke role' }))

    await waitFor(() => {
      expect(handlers.deleteCalls()).toBe(1)
    })
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Revoke planner at F1' })).not.toBeInTheDocument()
    })
  })

  it('does not call the server when the confirm dialog is cancelled', async () => {
    const handlers = installHandlers()
    const { user } = renderRoute('/f/F1/admin')
    await user.click(await screen.findByRole('tab', { name: 'Memberships' }))
    await user.click(await screen.findByRole('button', { name: 'Revoke planner at F1' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(handlers.deleteCalls()).toBe(0)
    expect(screen.getByRole('button', { name: 'Revoke planner at F1' })).toBeInTheDocument()
  })
})
