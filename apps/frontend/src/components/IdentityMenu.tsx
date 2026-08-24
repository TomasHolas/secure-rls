/**
 * IdentityMenu - the identity chip as a control: the `TenantPill` brick with a chevron, opening
 * a small menu that carries sign-out (issue #114, pattern from beautifului.dev, whose rail leads
 * with the account and hides the session actions behind it).
 *
 * The panel is `position: fixed` and placed off the trigger's own box, so it escapes the clip
 * the collapsed rail is drawn with instead of being cut off at the rail's edge. It closes on an
 * outside pointerdown and on Escape, which returns focus to the trigger.
 *
 * Display only, like the pill it wraps: the tenant and the user are what the server already
 * believes from the verified token (`auth.ts`), never a choice offered here.
 */

import { useEffect, useRef, useState } from "react";

import { Icon } from "./Icon";
import { TenantPill } from "./TenantPill";

export function IdentityMenu({
  tenant,
  username,
  onSignOut,
}: {
  tenant: string;
  username: string;
  onSignOut: () => void;
}) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const label = `Signed in as ${username}, tenant ${tenant}`;

  function close(): void {
    setAt(null);
    trigger.current?.focus();
  }

  // The panel's own offset from this point is a margin in the stylesheet, not a number here.
  function open(): void {
    const box = trigger.current?.getBoundingClientRect();
    setAt({ top: box?.bottom ?? 0, left: box?.left ?? 0 });
  }

  useEffect(() => {
    if (at === null) return;
    function onPointerDown(event: PointerEvent): void {
      const target = event.target as Node;
      if (!panel.current?.contains(target) && !trigger.current?.contains(target)) setAt(null);
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") close();
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [at]);

  return (
    <div className="rail-identity-wrap">
      <button
        ref={trigger}
        type="button"
        className="rail-identity"
        onClick={() => (at === null ? open() : close())}
        aria-expanded={at !== null}
        aria-haspopup="menu"
        aria-label={label}
      >
        <TenantPill tenant={tenant} username={username} />
        <Icon name="chevron-down" size={14} className="rail-identity-chevron rail-copy" />
      </button>
      {at === null ? null : (
        <div ref={panel} className="rail-menu" role="menu" style={at}>
          <button
            type="button"
            role="menuitem"
            className="rail-menu-item"
            onClick={() => {
              setAt(null);
              onSignOut();
            }}
          >
            <Icon name="x" size={15} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
