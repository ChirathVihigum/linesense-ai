/** Permission names from backend-contracts.md §4 (`app/auth/policy.py`). */
export type Permission =
  | 'order:read'
  | 'analysis:read'
  | 'inventory:read'
  | 'capacity:read'
  | 'ie:read'
  | 'quality:read'
  | 'document:read'
  | 'order:create'
  | 'order:import'
  | 'order:transition'
  | 'order:dispatch'
  | 'order:cancel'
  | 'analysis:run'
  | 'recommendation:decide'
  | 'recommendation:apply'
  | 'inventory:write'
  | 'ie:write'
  | 'quality:inspect'
  | 'quality:hold'
  | 'quality:release'
  | 'document:upload'
  | 'note:create'
  | 'audit:read'
  | 'admin:manage'

/**
 * Whether the signed-in user holds `permission` in a factory. The server is the
 * authority (it re-checks every request); this only decides what the UI offers.
 */
export function hasPermission(
  factory: { permissions: readonly string[] } | null | undefined,
  permission: Permission,
): boolean {
  return factory?.permissions.includes(permission) ?? false
}
