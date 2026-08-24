/**
 * InlineSearch - a search box that grows right-to-left out of its own icon control and focuses
 * itself (issue #114, pattern from beautifului.dev): closed it is one icon at the end of a row,
 * open it takes the row, and Escape closes it, clears the query and hands focus back to the icon.
 *
 * The query is the caller's state, so the caller filters what it has already loaded and this
 * brick owns nothing but the disclosure. `hidden` is for a container that clips it out of view:
 * the box closes, clears, and both controls leave the Tab order and the accessibility tree.
 */

import { useEffect, useRef, useState } from "react";

import { Icon } from "./Icon";

export function InlineSearch({
  id,
  label,
  placeholder,
  value,
  onChange,
  hidden,
}: {
  id: string;
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  hidden?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLInputElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) box.current?.focus();
  }, [open]);

  // Clipped out of view: a box a reader cannot see must not keep their list filtered.
  useEffect(() => {
    if (!hidden) return;
    setOpen(false);
    onChange("");
  }, [hidden, onChange]);

  function close(): void {
    setOpen(false);
    onChange("");
    toggle.current?.focus();
  }

  return (
    <div className="rail-search" data-open={open ? "true" : "false"}>
      <input
        ref={box}
        id={id}
        type="search"
        className="rail-search-input"
        placeholder={placeholder}
        aria-label={label}
        aria-hidden={open && !hidden ? undefined : true}
        tabIndex={open && !hidden ? undefined : -1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") close();
        }}
      />
      <button
        ref={toggle}
        type="button"
        className="btn-icon rail-search-toggle"
        onClick={() => (open ? close() : setOpen(true))}
        aria-expanded={open}
        aria-controls={id}
        aria-label={open ? `Close ${label.toLowerCase()}` : label}
        title={open ? `Close ${label.toLowerCase()}` : label}
        aria-hidden={hidden || undefined}
        tabIndex={hidden ? -1 : undefined}
      >
        <Icon name={open ? "x" : "search"} size={16} />
      </button>
    </div>
  );
}
