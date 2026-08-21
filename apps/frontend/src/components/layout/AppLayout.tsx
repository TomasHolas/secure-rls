// The app shell: header plus the main content region.

import type { ReactNode } from "react";

import { Header } from "./Header";

export function AppLayout({
  tenantBadge,
  children,
}: {
  tenantBadge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="app">
      <Header tenantBadge={tenantBadge} />
      <main className="main">{children}</main>
    </div>
  );
}
