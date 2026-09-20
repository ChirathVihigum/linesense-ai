import clsx from 'clsx'
import { useEffect, useId, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'

import { Icon } from '../components/Icon'
import { NotificationsMenu } from '../features/notifications/NotificationsMenu'
import { toApiError } from '../lib/api'
import { useLogout, useMe } from '../lib/auth'
import { useCan, useFactory } from '../lib/factory'
import { humanizeCode } from '../lib/format'
import type { Permission } from '../lib/permissions'

interface NavItem {
  label: string
  /** Path relative to `/f/:factoryCode/`. */
  to: string
  permission: Permission
  end?: boolean
}

interface NavSection {
  label: string
  permission: Permission
  items: NavItem[]
}

/** Sections are listed only when the user may read them; actions only when permitted. */
export const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Overview',
    permission: 'order:read',
    items: [{ label: 'Overview', to: 'overview', permission: 'order:read', end: true }],
  },
  {
    label: 'Orders',
    permission: 'order:read',
    items: [
      { label: 'All orders', to: 'orders', permission: 'order:read', end: true },
      { label: 'New order', to: 'orders/new', permission: 'order:create' },
      { label: 'Import orders', to: 'orders/import', permission: 'order:import' },
    ],
  },
  {
    label: 'Planning',
    permission: 'capacity:read',
    items: [{ label: 'Planning board', to: 'planning', permission: 'capacity:read', end: true }],
  },
  {
    label: 'Approvals',
    permission: 'analysis:read',
    items: [{ label: 'Approval inbox', to: 'approvals', permission: 'analysis:read', end: true }],
  },
  {
    label: 'Materials',
    permission: 'inventory:read',
    items: [{ label: 'Materials', to: 'materials', permission: 'inventory:read', end: true }],
  },
  {
    label: 'Industrial engineering',
    permission: 'ie:read',
    items: [{ label: 'Line balance', to: 'ie', permission: 'ie:read', end: true }],
  },
  {
    label: 'Quality',
    permission: 'quality:read',
    items: [{ label: 'Inspections and holds', to: 'quality', permission: 'quality:read', end: true }],
  },
  {
    label: 'Notes',
    permission: 'note:create',
    items: [{ label: 'Notes', to: 'notes', permission: 'note:create', end: true }],
  },
  {
    label: 'Knowledge base',
    permission: 'document:read',
    items: [{ label: 'Documents and search', to: 'knowledge', permission: 'document:read', end: true }],
  },
  {
    label: 'Administration',
    permission: 'admin:manage',
    items: [{ label: 'Administration', to: 'admin', permission: 'admin:manage', end: true }],
  },
]

function FactorySelector() {
  const me = useMe()
  const factory = useFactory()
  const navigate = useNavigate()
  const location = useLocation()

  // Keep the current section (e.g. "orders") when switching factory; deeper
  // paths refer to factory-specific records, so they are not carried over.
  const section = location.pathname.split('/')[3] ?? ''

  return (
    <div className="flex items-center gap-2">
      <Icon name="factory" className="h-4 w-4 text-fg-muted" />
      <label htmlFor="factory-selector" className="sr-only">
        Factory
      </label>
      <select
        id="factory-selector"
        className="input h-8 w-auto py-0"
        value={factory.code}
        onChange={(event) => {
          void navigate(`/f/${encodeURIComponent(event.target.value)}/${section}`)
        }}
      >
        {me.factories.map((f) => (
          <option key={f.id} value={f.code}>
            {f.name} ({f.code})
          </option>
        ))}
      </select>
    </div>
  )
}

function UserMenu() {
  const me = useMe()
  const factory = useFactory()
  const logout = useLogout()
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [open])

  return (
    <div
      ref={containerRef}
      className="relative"
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false)
      }}
    >
      <button
        type="button"
        className="inline-flex h-8 items-center gap-1 rounded-md px-2 font-medium hover:bg-surface-sunken"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => {
          setOpen((value) => !value)
        }}
      >
        {me.user.display_name}
        <Icon name="caret-down" className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          id={menuId}
          className="panel absolute right-0 z-30 mt-2 w-72 p-4 shadow-lg shadow-zinc-950/10"
        >
          <p className="font-semibold">{me.user.display_name}</p>
          <p className="text-fg-muted">{me.user.email}</p>
          <p className="mt-1 text-fg-muted">{me.organization.name}</p>
          <p className="mt-3 font-medium">Roles at {factory.name}</p>
          <ul aria-label={`Roles at ${factory.name}`} className="mt-1 flex flex-wrap gap-1">
            {factory.roles.map((role) => (
              <li key={role} className="rounded-full border border-neutral-line bg-neutral-bg px-2 py-0.5 text-xs text-neutral-fg">
                {humanizeCode(role)}
              </li>
            ))}
          </ul>
          {logout.isError && (
            <p role="alert" className="mt-3 text-bad-fg">
              Sign out failed: {toApiError(logout.error).message}
            </p>
          )}
          <button
            type="button"
            className="btn-secondary mt-4 w-full justify-center"
            disabled={logout.isPending}
            onClick={() => {
              logout.mutate()
            }}
          >
            <Icon name="sign-out" />
            {logout.isPending ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      )}
    </div>
  )
}

function Sidebar({ id, open }: { id: string; open: boolean }) {
  const factory = useFactory()
  const can = useCan()
  const sections = NAV_SECTIONS.filter((section) => can(section.permission))

  return (
    <nav
      id={id}
      aria-label="Main"
      className={clsx(
        'w-56 shrink-0 border-r border-line bg-surface px-3 py-4',
        open ? 'block' : 'hidden lg:block',
      )}
    >
      {sections.map((section) => (
        <div key={section.label} className="mb-6">
          <h2 className="mb-1 px-3 text-xs font-medium text-fg-muted">
            {section.label}
          </h2>
          <ul className="flex flex-col gap-0.5">
            {section.items
              .filter((item) => can(item.permission))
              .map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={`/f/${encodeURIComponent(factory.code)}/${item.to}`}
                    end={item.end}
                    className={({ isActive }) =>
                      clsx(
                        'block rounded-md px-3 py-1.5',
                        isActive
                          ? 'bg-accent-soft font-medium text-accent'
                          : 'text-fg hover:bg-surface-sunken',
                      )
                    }
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}

export function Layout() {
  const factory = useFactory()
  const location = useLocation()
  const [navOpen, setNavOpen] = useState(false)
  const sidebarId = useId()

  // Close the collapsed (< 1024 px) navigation after moving to another page.
  const [lastPath, setLastPath] = useState(location.pathname)
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname)
    setNavOpen(false)
  }

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <a
        href="#main-content"
        className="sr-only z-50 rounded-md bg-surface px-4 py-2 font-medium text-accent focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to main content
      </a>
      <header className="flex h-14 items-center gap-4 border-b border-line bg-surface px-4">
        <button
          type="button"
          className="btn-secondary h-8 lg:hidden"
          aria-expanded={navOpen}
          aria-controls={sidebarId}
          onClick={() => {
            setNavOpen((value) => !value)
          }}
        >
          <Icon name="menu" />
          Menu
        </button>
        <span className="flex items-center gap-2 text-base font-semibold tracking-tight">
          <Icon name="factory" className="h-5 w-5 text-accent" />
          LineSense AI
        </span>
        <div className="ml-auto flex items-center gap-3">
          <FactorySelector />
          <NotificationsMenu />
          <UserMenu />
        </div>
      </header>
      <div className="flex flex-1">
        <Sidebar id={sidebarId} open={navOpen} />
        <main id="main-content" tabIndex={-1} className="mx-auto w-full max-w-[1400px] min-w-0 flex-1 p-6 focus:outline-none">
          <p className="sr-only">Factory: {factory.name}</p>
          {/* Remount pages on factory switch so no filter or form state leaks across factories. */}
          <Outlet key={factory.id} />
        </main>
      </div>
    </div>
  )
}
