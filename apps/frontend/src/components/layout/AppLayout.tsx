// The app shell: header, the optional left rail, and the main content region.

import type { ReactNode } from "react";

import { Header } from "./Header";

export function AppLayout({
  tenantBadge,
  sidebar,
  children,
}: {
  tenantBadge?: ReactNode;
  sidebar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app">
      <Header tenantBadge={tenantBadge} />
      <div className="app-body">
        {sidebar}
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
