// The header's tenantBadge slot: the identity pill plus logout.

import { clearSession, type Session } from "../auth";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { TenantPill } from "../components/TenantPill";

export function SessionBadge({ session }: { session: Session }) {
  return (
    <>
      <TenantPill tenant={session.tenantId} username={session.username} />
      <Button onClick={clearSession} title="Sign out">
        <Icon name="x" size={15} /> Sign out
      </Button>
    </>
  );
}
