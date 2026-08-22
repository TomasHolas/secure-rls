/**
 * Tabs — the shell's top-level sections (ADR 0014). It lives in the header rather than in a
 * view, because the sections are siblings of each other and none of them owns the others; the
 * shell keeps them all mounted so switching never costs a reader the state of the one they left.
 *
 * A `<button role="tab">` per section over KB's `.pill` metrics, with `aria-selected` carrying
 * the state a screen reader needs and `.active` the one an eye does.
 */

import { Icon } from "../Icon";

export interface Tab {
  id: string;
  label: string;
  icon?: string;
}

export function Tabs({
  tabs,
  active,
  onSelect,
  label = "Sections",
}: {
  tabs: Tab[];
  active: string;
  onSelect: (id: string) => void;
  label?: string;
}) {
  return (
    <nav className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === active}
          className={tab.id === active ? "tab active" : "tab"}
          onClick={() => onSelect(tab.id)}
        >
          {tab.icon ? <Icon name={tab.icon} size={15} /> : null}
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
