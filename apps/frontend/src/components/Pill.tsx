/**
 * Pill — the small status chip brick (KB's `.pill` base plus its tone variants). One
 * shape for every short status label in the app: a turn's verdict, a truncation
 * notice, a retry counter, the active model. `TenantPill` stays its own brick because
 * it is the identity chip, not a status.
 */

import type { ReactNode } from "react";

import { Icon } from "./Icon";

export type PillTone = "neutral" | "accent" | "ok" | "warn" | "danger";

export function Pill({
  tone = "neutral",
  icon,
  title,
  children,
}: {
  tone?: PillTone;
  icon?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className={`pill pill-${tone}`} title={title}>
      {icon ? <Icon name={icon} size={13} /> : null}
      {children}
    </span>
  );
}
