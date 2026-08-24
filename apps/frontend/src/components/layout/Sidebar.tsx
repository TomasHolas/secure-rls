/**
 * Sidebar - the shell's left rail, and the collapse mechanism the rest of the rail is built on
 * (issue #114, pattern from beautifului.dev): the column inside the aside is always laid out at
 * the expanded width and the aside clips it, so collapsing animates one width, re-lays out
 * nothing and cannot move an icon sideways when the copy beside it leaves.
 *
 * The brick owns its collapse state the way KB's Collapsible owns its open flag, so no view
 * threads it through, and publishes it through `useSidebarCollapsed`: a slot whose controls end
 * up clipped reads that and takes them out of the Tab order and the accessibility tree, which is
 * what keeps a collapsed rail from hiding focusable controls behind its own edge.
 *
 * Slots, top to bottom: `identity` (the signed-in identity and its menu), the head row carrying
 * the collapse toggle, the caps `title` and the `search` control, `actions`, then the list.
 * Copy that should fade out rather than be cut off mid-word by the clip carries `rail-copy`.
 */

import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";

import { Icon } from "../Icon";

const CollapsedContext = createContext(false);

/** True while the rail is clipped to its icon column - what a slot's hidden controls read. */
export function useSidebarCollapsed(): boolean {
  return useContext(CollapsedContext);
}

export function Sidebar({
  title,
  identity,
  search,
  actions,
  children,
}: {
  title: string;
  identity?: ReactNode;
  search?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const label = collapsed ? `Show ${title.toLowerCase()}` : `Hide ${title.toLowerCase()}`;

  return (
    <CollapsedContext.Provider value={collapsed}>
      <aside className={collapsed ? "sidebar sidebar-collapsed" : "sidebar"}>
        <div className="sidebar-inner">
          {identity ? <div className="sidebar-identity">{identity}</div> : null}
          <div className="sidebar-head">
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
            <span className="sidebar-title rail-copy" aria-hidden={collapsed || undefined}>
              {title}
            </span>
            {search}
          </div>
          {actions ? <div className="sidebar-actions">{actions}</div> : null}
          <div className="sidebar-body rail-copy">{children}</div>
        </div>
      </aside>
    </CollapsedContext.Provider>
  );
}
