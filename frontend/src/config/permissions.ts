import type { Role } from '@/types'

/**
 * Single source of truth for the PRD permission matrix. Route guards, the
 * sidebar, and the dashboard all read from this map so they cannot drift
 * apart. Admin is NOT a superuser — every route lists its allowed roles
 * explicitly, admin included only where the PRD grants admin that page.
 */
export const ROUTE_ROLES: Record<string, Role[]> = {
  '/dashboard': ['admin', 'inward_operator', 'packer'],

  // Inward workflow — inward_operator only
  '/upload-po': ['inward_operator'],
  '/create-box': ['inward_operator'],
  '/scan': ['inward_operator'],
  '/active-boxes': ['inward_operator'],
  '/close-box': ['inward_operator'],

  // Management — admin only
  '/customers': ['admin'],
  '/reports': ['admin'],

  // Outward workflow
  '/outward/upload-po': ['admin'],
  '/outward/create-box': ['packer'],
  '/outward/create-box-labels': ['packer'],
  '/outward/scan': ['packer'],
  '/outward/active-boxes': ['packer'],
  '/outward/close-box': ['packer'],

  // Administration — admin only
  '/admin/users': ['admin'],
  '/admin/audit-logs': ['admin'],
}

export function canAccessRoute(roles: Role[], path: string): boolean {
  const allowed = ROUTE_ROLES[path]
  if (!allowed) return true
  return roles.some((r) => allowed.includes(r))
}
