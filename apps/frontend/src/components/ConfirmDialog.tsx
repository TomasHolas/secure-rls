/**
 * ConfirmDialog - the confirm step in front of an irreversible action, on the Modal and
 * Button bricks (ported from the knowledgebase, ADR 0006). Every delete in the app goes
 * through it: the action runs only after the reader confirms.
 */

import type { ReactNode } from "react";

import { Button } from "./Button";
import { Modal } from "./Modal";

export function ConfirmDialog({
  open,
  title = "Are you sure?",
  message,
  confirmLabel = "Delete",
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title?: string;
  message: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal open={open} onClose={onCancel} title={title} width={420}>
      <div className="confirm-body">{message}</div>
      <div className="confirm-actions">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="ghost" className="btn-danger" onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
