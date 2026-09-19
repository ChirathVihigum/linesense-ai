import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmDialog } from './ConfirmDialog'

function Harness() {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          setOpen(true)
        }}
      >
        Open dialog
      </button>
      <ConfirmDialog
        open={open}
        title="Delete this order?"
        confirmLabel="Delete"
        onConfirm={() => {
          setOpen(false)
        }}
        onCancel={() => {
          setOpen(false)
        }}
      >
        This cannot be undone.
      </ConfirmDialog>
    </div>
  )
}

describe('ConfirmDialog', () => {
  it('moves focus to Cancel on open and traps Tab inside the dialog', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.click(screen.getByRole('button', { name: 'Open dialog' }))

    const cancel = screen.getByRole('button', { name: 'Cancel' })
    const confirm = screen.getByRole('button', { name: 'Delete' })
    expect(cancel).toHaveFocus()

    await user.tab()
    expect(confirm).toHaveFocus()

    // Tab from the last focusable element wraps back to the first.
    await user.tab()
    expect(cancel).toHaveFocus()

    // Shift+Tab from the first focusable element wraps back to the last.
    await user.tab({ shift: true })
    expect(confirm).toHaveFocus()
  })

  it('restores focus to the opener once the dialog closes', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Open dialog' })
    opener.focus()

    await user.click(opener)
    expect(await screen.findByRole('button', { name: 'Cancel' })).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('does not close on Escape while pending', async () => {
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(
      <ConfirmDialog open title="Working" confirmLabel="Confirm" pending onConfirm={vi.fn()} onCancel={onCancel}>
        Please wait.
      </ConfirmDialog>,
    )
    await user.keyboard('{Escape}')
    expect(onCancel).not.toHaveBeenCalled()
  })
})
