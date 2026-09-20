import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { SourceLabel } from '../../components/SourceLabel'
import { StateBadge } from '../../components/StateBadge'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDate, formatDateTime, formatDecimal, formatInteger, formatPercent } from '../../lib/format'

type Dashboard = Schemas['DashboardOut']

function Card({
  title,
  action,
  children,
}: {
  title: string
  action?: { to: string; label: string }
  children: ReactNode
}) {
  return (
    <section className="panel flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{title}</h2>
        {action && (
          <Link className="link text-sm" to={action.to}>
            {action.label}
          </Link>
        )}
      </div>
      {children}
    </section>
  )
}

function UtilizationBar({ fraction }: { fraction: string | null }) {
  const value = fraction === null ? null : Number(fraction)
  const pct = value === null || Number.isNaN(value) ? 0 : Math.max(0, Math.min(1, value))
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 overflow-hidden rounded-md border border-line bg-surface-sunken" aria-hidden="true">
        <div className="h-full bg-fg-muted" style={{ width: `${pct * 100}%` }} />
      </div>
      <span className="tabular-nums text-fg-muted">{formatPercent(value)}</span>
    </div>
  )
}

function OverviewContent() {
  const factory = useFactory()
  const factoryPath = `/f/${encodeURIComponent(factory.code)}`

  const query = useQuery({
    queryKey: ['dashboard', factory.id],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/dashboard', {
          params: { path: { factory_id: factory.id } },
        }),
      ),
    refetchInterval: 60_000,
  })

  if (query.isPending) return <LoadingState label="Loading the operations overview…" variant="block" rows={4} />
  if (query.isError) {
    return query.error instanceof ApiError && query.error.status === 403 ? (
      <PermissionDenied />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }

  const dashboard: Dashboard = query.data

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <SourceLabel source={{ kind: 'calculated' }} />
        <p className="text-sm text-fg-muted">
          As of {formatDateTime(dashboard.generated_at, factory.timezone)}
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Orders at risk" action={{ to: `${factoryPath}/orders`, label: 'View orders' }}>
          {dashboard.orders_at_risk.length === 0 ? (
            <p className="text-fg-muted">No orders are at risk in the next 7 days.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {dashboard.orders_at_risk.slice(0, 8).map((order) => (
                <li key={order.id} className="flex items-center justify-between gap-3 text-sm">
                  <div>
                    <span className="font-mono text-xs font-medium">{order.external_ref}</span>
                    <span className="ml-2 text-fg-muted">Due {formatDate(order.due_date)}</span>
                  </div>
                  <StateBadge vocabulary="material" state={order.material_state} />
                </li>
              ))}
            </ul>
          )}
          {dashboard.orders_at_risk.length > 8 && (
            <p className="text-xs text-fg-muted">
              And {formatInteger(dashboard.orders_at_risk.length - 8)} more.
            </p>
          )}
        </Card>

        <Card title="Material shortages" action={{ to: `${factoryPath}/materials`, label: 'View materials' }}>
          {dashboard.material_shortages.length === 0 ? (
            <p className="text-fg-muted">No material shortages against open demand.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {dashboard.material_shortages.slice(0, 8).map((material) => (
                <li key={material.material_id} className="flex items-center justify-between gap-3 text-sm">
                  <div>
                    <div>{material.material_name}</div>
                    <div className="font-mono text-xs text-fg-muted">{material.material_code}</div>
                  </div>
                  <div className="text-right tabular-nums">
                    <div>{formatDecimal(material.shortage_qty)} short</div>
                    {material.below_reorder_point && (
                      <div className="text-xs text-bad-fg">Below reorder point</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Quality holds" action={{ to: `${factoryPath}/quality`, label: 'View quality' }}>
          {dashboard.quality_holds.length === 0 ? (
            <p className="text-fg-muted">No active quality holds.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {dashboard.quality_holds.slice(0, 8).map((hold) => (
                <li key={hold.id} className="text-sm">
                  <span className="font-mono text-xs font-medium">{hold.order_external_ref}</span>
                  <span className="ml-2 text-fg-muted">{hold.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Active analysis runs">
          {dashboard.active_runs.length === 0 ? (
            <p className="text-fg-muted">No analysis runs are in progress.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {dashboard.active_runs.slice(0, 8).map((run) => (
                <li key={run.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="font-mono text-xs font-medium">{run.order_external_ref}</span>
                  <StateBadge vocabulary="analysis" state={run.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Pending approvals">
          <p className="text-2xl font-semibold tabular-nums">{formatInteger(dashboard.pending_approvals)}</p>
          <p className="text-fg-muted">Recommendations awaiting a supervisor's decision.</p>
        </Card>

        <Card title="Capacity, next 7 days" action={{ to: `${factoryPath}/planning`, label: 'View planning board' }}>
          {dashboard.capacity_next_7_days.length === 0 ? (
            <EmptyState icon="factory" title="No active lines" description="No active production lines are configured for this factory." />
          ) : (
            <ul className="flex flex-col gap-2">
              {dashboard.capacity_next_7_days.map((line) => (
                <li key={line.line_id} className="flex items-center justify-between gap-3 text-sm">
                  <div>
                    <div>{line.line_name}</div>
                    <div className="font-mono text-xs text-fg-muted">{line.line_code}</div>
                  </div>
                  <UtilizationBar fraction={line.utilization_fraction} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

export function OverviewPage() {
  const can = useCan()
  return (
    <>
      <PageHeader
        title="Overview"
        description="A calculated snapshot of orders, materials, quality and capacity. No AI calls are made to build this page."
      />
      {can('order:read') ? (
        <OverviewContent />
      ) : (
        <PermissionDenied message="Your role does not include access to the operations overview." />
      )}
    </>
  )
}
