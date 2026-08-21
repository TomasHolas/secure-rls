// App header: the brand mark, the app name, and the trailing slot the tenant badge fills once logged in.

import type { ReactNode } from "react";

export function Header({ tenantBadge }: { tenantBadge?: ReactNode }) {
  return (
    <header className="header">
      <div className="brand">
        <span className="mark">
          <img src="/anteater.png" alt="" style={{ height: 26, width: "auto", display: "block" }} />
        </span>
        <span className="name">
          secure<span className="accent">-rls</span>
        </span>
      </div>

      <div className="header-actions">{tenantBadge}</div>
    </header>
  );
}
