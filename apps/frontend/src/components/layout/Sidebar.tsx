/**
 * Sidebar - the shell's left rail: a caps title, an actions slot and the list itself. The
 * collapse state is the brick's own, the way KB's Collapsible owns its open flag, so no
 * view has to thread it through; collapsed the rail keeps only the reopen control, giving
 * the transcript the full width.
 */

import { useState } from "react";
import type { ReactNode } from "react";

import { Icon } from "../Icon";

export function Sidebar({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const label = collapsed ? `Show ${title.toLowerCase()}` : `Hide ${title.toLowerCase()}`;

  return (
    <aside className={collapsed ? "sidebar sidebar-collapsed" : "sidebar"}>
      <div className="sidebar-head">
        {collapsed ? null : <span className="sidebar-title">{title}</span>}
        <button
          type="button"
          className="btn-icon sidebar-toggle"
          onClick={() => setCollapsed((previous) => !previous)}
          aria-expanded={!collapsed}
          aria-label={label}
          title={label}
        >
          <Icon name={collapsed ? "arrow-right" : "arrow-left"} size={16} />
        </button>
      </div>
      {collapsed ? null : (
        <>
          {actions ? <div className="sidebar-actions">{actions}</div> : null}
          <div className="sidebar-body">{children}</div>
        </>
      )}
    </aside>
  );
}
