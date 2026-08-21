// Centered empty/error state with a leading icon.

import type { ReactNode } from "react";

import { Icon } from "../Icon";

export function EmptyState({
  icon = "inbox",
  size = 40,
  children,
}: {
  icon?: string;
  size?: number;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="ei">
        <Icon name={icon} size={size} />
      </div>
      {children}
    </div>
  );
}
