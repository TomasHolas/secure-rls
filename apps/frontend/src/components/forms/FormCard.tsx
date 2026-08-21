/**
 * FormCard — the centered card a standalone form sits in: title, optional
 * subtitle, the fields (children, ending in a `btn-block` Button), and the error
 * slot. Submitting is a real form submit, so Enter works from any field.
 * Ported from KB's `.capture-card` / `.capture-error` shape.
 */

import type { ReactNode } from "react";

export function FormCard({
  title,
  subtitle,
  error,
  onSubmit,
  children,
}: {
  title: string;
  subtitle?: string;
  error?: string | null;
  onSubmit: () => void;
  children: ReactNode;
}) {
  return (
    <div className="form-card-wrap">
      <form
        className="form-card"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        <div className="form-card-head">
          <h2 className="form-card-title">{title}</h2>
          {subtitle && <p className="form-card-sub">{subtitle}</p>}
        </div>

        {children}

        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
      </form>
    </div>
  );
}
