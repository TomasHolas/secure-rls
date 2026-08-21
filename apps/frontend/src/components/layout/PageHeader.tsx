// The standard page head: eyebrow, title, subtitle, plus optional trailing content.

import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      {eyebrow !== undefined && eyebrow !== null && <div className="eyebrow">{eyebrow}</div>}
      <div className="page-title">{title}</div>
      {subtitle !== undefined && subtitle !== null && <div className="page-sub">{subtitle}</div>}
      {actions}
    </div>
  );
}
