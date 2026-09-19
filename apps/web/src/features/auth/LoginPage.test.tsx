import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { safeNextPath } from './safeNextPath'

describe('LoginPage', () => {
  it('links to the backend login with the requested next path', async () => {
    renderRoute('/login?next=%2Ff%2FF1%2Forders%3Fq%3DPO')
    expect(await screen.findByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      '/auth/login?next=%2Ff%2FF1%2Forders%3Fq%3DPO',
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('explains a failed sign-in', async () => {
    renderRoute('/login?error=auth_failed')
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign-in did not complete.')
  })

  it('only accepts same-app paths as next', () => {
    expect(safeNextPath('/f/F1/orders')).toBe('/f/F1/orders')
    expect(safeNextPath('https://evil.test/')).toBe('/')
    expect(safeNextPath('//evil.test/')).toBe('/')
    expect(safeNextPath('/login?next=/x')).toBe('/')
    expect(safeNextPath(null)).toBe('/')
  })
})
