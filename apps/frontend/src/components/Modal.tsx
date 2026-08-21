/**
 * Modal - the one dialog brick, ported from the knowledgebase (ADR 0006). A dimmed
 * backdrop over the current page, a centered panel with a title and a close control, and
 * the expected dismissals: Escape, backdrop click, the close button. Body scroll is locked
 * while open and the panel renders through a portal, so no view has to host it.
 */

import { useEffect } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";

export function Modal({
  open,
  onClose,
  title,
  children,
  width = 500,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        style={{ maxWidth: width }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          {title ? <div className="modal-title">{title}</div> : <span />}
          <button className="modal-close" onClick={onClose} aria-label="Close" title="Close (Esc)">
            &times;
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
