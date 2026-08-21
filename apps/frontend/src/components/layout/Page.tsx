// A page wrapper: the shared ".page" container every view sits in.

import type { CSSProperties, ReactNode } from "react";

export function Page({
  className,
  style,
  children,
}: {
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  return (
    <div className={className ? `page ${className}` : "page"} style={style}>
      {children}
    </div>
  );
}
