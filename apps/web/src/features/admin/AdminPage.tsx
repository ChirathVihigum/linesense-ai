import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { Tabs } from '../../components/Tabs'
import { useCan } from '../../lib/factory'
import { AuditLogTable } from './AuditLogTable'
import { BudgetSettings } from './BudgetSettings'
import { MembershipTable } from './MembershipTable'
import { PolicyList } from './PolicyList'

export function AdminPage() {
  const can = useCan()
  return (
    <>
      <PageHeader title="Administration" description="Membership and role management, audit history, quality policies and effective run limits." />
      {can('admin:manage') ? (
        <Tabs
          label="Administration"
          tabs={[
            { id: 'audit', label: 'Audit log', content: <AuditLogTable /> },
            { id: 'memberships', label: 'Memberships', content: <MembershipTable /> },
            { id: 'policies', label: 'Quality policies', content: <PolicyList /> },
            { id: 'settings', label: 'Settings', content: <BudgetSettings /> },
          ]}
        />
      ) : (
        <PermissionDenied message="Only organization administrators can access administration." />
      )}
    </>
  )
}
