// The app shell: header (with the optional tab strip), the optional left rail, and the main region.

import type { ReactNode } from "react";

import { Header } from "./Header";

export function AppLayout({
  tabs,
  tenantBadge,
  sidebar,
  children,
}: {
  tabs?: ReactNode;
  tenantBadge?: ReactNode;
  sidebar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app">
      <Header tabs={tabs} tenantBadge={tenantBadge} />
      <div className="app-body">
        {sidebar}
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
