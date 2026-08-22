// App header: the brand mark, the app name, the tab strip slot, and the trailing tenant-badge slot.

import type { ReactNode } from "react";

export function Header({ tabs, tenantBadge }: { tabs?: ReactNode; tenantBadge?: ReactNode }) {
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

      {tabs}

      <div className="header-actions">{tenantBadge}</div>
    </header>
  );
}
