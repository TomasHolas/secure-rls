/**
 * Button — the one button brick. Variants: primary | ghost. Compose an <Icon> as a
 * child. Use this instead of hand-writing `<button className="btn ...">` anywhere.
 */

import type { ReactNode } from "react";

type Variant = "primary" | "ghost";

export function Button({
  onClick,
  disabled,
  type = "button",
  variant = "ghost",
  className,
  title,
  children,
}: {
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
  variant?: Variant;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  const cls = `btn btn-${variant}${className ? ` ${className}` : ""}`;
  return (
    <button type={type} className={cls} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}
