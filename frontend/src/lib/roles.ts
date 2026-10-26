import type { Role } from './types'

/** Settings are per person; break-glass has no person to configure. The API enforces the same. */
export function canSeeSettings(role: Role): boolean {
  return role === 'owner' || role === 'admin' || role === 'member'
}

/** Team, categories and new channels. The API enforces owner/admin on each route. */
export function canManageTeam(role: Role): boolean {
  return role === 'owner' || role === 'admin'
}

export const ROLE_LABEL: Record<Role, string> = {
  owner: 'Owner',
  admin: 'Admin',
  member: 'Member',
  operator: 'Operator access',
}
