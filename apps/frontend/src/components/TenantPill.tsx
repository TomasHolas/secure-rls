/**
 * TenantPill — the identity chip: which tenant the session is scoped to and who
 * is signed in. Display only; the server derives the real tenant from the
 * verified JWT. Ported from KB's `.category-pill` shape.
 */

import { Icon } from "./Icon";

export function TenantPill({ tenant, username }: { tenant: string; username: string }) {
  return (
    <span className="tenant-pill" title={`Signed in as ${username}, tenant ${tenant}`}>
      <Icon name="database" size={13} />
      <span className="tenant-pill-tenant">{tenant}</span>
      <span className="tenant-pill-user">{username}</span>
    </span>
  );
}
