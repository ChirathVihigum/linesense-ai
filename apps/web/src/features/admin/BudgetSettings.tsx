import { useQuery } from '@tanstack/react-query'

import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { SourceLabel } from '../../components/SourceLabel'
import { api, unwrap } from '../../lib/api'
import { formatInteger } from '../../lib/format'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-line py-2 last:border-0">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="font-mono tabular-nums">{value}</dd>
    </div>
  )
}

/** Read-only effective run limits (`admin:manage`): nothing here is editable, and
 * none of it is set by a model. */
export function BudgetSettings() {
  const query = useQuery({
    queryKey: ['admin-settings'],
    queryFn: () => unwrap(api.GET('/api/v1/admin/settings')),
  })

  if (query.isPending) return <LoadingState label="Loading settings…" variant="block" rows={3} />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  const settings = query.data
  return (
    <div className="flex flex-col gap-3">
      <SourceLabel source={{ kind: 'calculated' }} />
      <dl className="panel max-w-md p-4">
        <Row label="Model call limit per run" value={formatInteger(settings.model_calls_limit)} />
        <Row label="Token budget per run" value={formatInteger(settings.token_budget)} />
        <Row label="Run deadline (seconds)" value={formatInteger(settings.run_deadline_seconds)} />
        <Row label="Tool-call cap per agent invocation" value={formatInteger(settings.max_tool_calls)} />
        <Row label="Upload size limit (bytes)" value={formatInteger(settings.max_upload_bytes)} />
        <Row label="LLM provider" value={settings.llm_provider} />
        <Row label="LLM model" value={settings.llm_model} />
      </dl>
    </div>
  )
}
