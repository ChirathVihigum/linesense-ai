import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Icon } from '../../components/Icon'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime } from '../../lib/format'

type Notification = Schemas['NotificationOut']

const POLL_INTERVAL_MS = 30_000
const RECENT_LIMIT = 20

/**
 * Notifications carry a backend-relative `link` (e.g. `/orders/{id}`,
 * `/recommendations/{id}`) written before this frontend's `/f/:factoryCode/...`
 * routing existed. Map the shapes that have a real screen; anything else
 * renders as plain text rather than a link to nowhere.
 */
function resolveNotificationLink(link: string | null, factoryCode: string): string | null {
  if (!link) return null
  const prefix = `/f/${encodeURIComponent(factoryCode)}`
  const orderMatch = /^\/orders\/(.+)$/.exec(link)
  if (orderMatch) return `${prefix}/orders/${orderMatch[1]}`
  const recommendationMatch = /^\/recommendations\/(.+)$/.exec(link)
  if (recommendationMatch) return `${prefix}/approvals/${recommendationMatch[1]}`
  return null
}

export function NotificationsMenu() {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const containerRef = useRef<HTMLDivElement>(null)

  const queryKey = ['notifications', factory.id] as const
  const query = useQuery({
    queryKey,
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/notifications', {
          params: { path: { factory_id: factory.id }, query: { limit: RECENT_LIMIT, offset: 0 } },
        }),
      ),
    refetchInterval: POLL_INTERVAL_MS,
  })

  const markRead = useMutation({
    mutationFn: (notificationId: string) =>
      unwrap(api.POST('/api/v1/notifications/{notification_id}/read', { params: { path: { notification_id: notificationId } } })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey })
    },
  })

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

  const items: Notification[] = query.data?.items ?? []
  const unreadCount = items.filter((item) => item.read_at === null).length

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
        className="relative inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-surface-sunken"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={unreadCount > 0 ? `Notifications, ${unreadCount} unread` : 'Notifications'}
        onClick={() => {
          setOpen((value) => !value)
        }}
      >
        <Icon name="bell" className="h-4.5 w-4.5" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold text-accent-fg tabular-nums">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="Notifications"
          className="panel absolute right-0 z-30 mt-2 w-80 max-h-96 overflow-y-auto p-2 shadow-lg shadow-zinc-950/10"
        >
          {query.isPending && <p className="p-3 text-sm text-fg-muted">Loading notifications…</p>}
          {query.isError && (
            <p className="p-3 text-sm text-bad-fg">
              {query.error instanceof ApiError ? query.error.message : 'Notifications could not be loaded.'}
            </p>
          )}
          {query.isSuccess && items.length === 0 && (
            <p className="p-3 text-sm text-fg-muted">No notifications yet.</p>
          )}
          {query.isSuccess && items.length > 0 && (
            <ul className="flex flex-col gap-1">
              {items.map((item) => {
                const unread = item.read_at === null
                const resolvedLink = resolveNotificationLink(item.link, factory.code)
                const content = (
                  <>
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-medium">{item.title}</span>
                      {unread && <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-accent" aria-hidden="true" />}
                    </div>
                    <p className="mt-0.5 text-fg-muted">{item.body}</p>
                    <p className="mt-1 text-xs text-fg-muted">{formatDateTime(item.created_at, factory.timezone)}</p>
                  </>
                )
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="block w-full rounded-md p-2 text-left text-sm hover:bg-surface-sunken"
                      onClick={() => {
                        if (unread) markRead.mutate(item.id)
                        setOpen(false)
                      }}
                    >
                      {resolvedLink ? (
                        <Link to={resolvedLink} className="contents">
                          {content}
                        </Link>
                      ) : (
                        content
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
