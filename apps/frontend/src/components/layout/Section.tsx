/**
 * The app-wide content card: a small uppercase title above a clean rounded card
 * (the "Settings" look). Every page composes this so the whole app shares one
 * visual language. Label/control rows inside it use `.settings-row`.
 */

import type { ReactNode } from "react";

export function Section({
  title,
  aside,
  children,
}: {
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="settings-group">
      <div className="settings-group-head">
        <h4 className="settings-group-title">{title}</h4>
        {aside}
      </div>
      <div className="settings-card">{children}</div>
    </section>
  );
}
