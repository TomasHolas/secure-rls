/**
 * ParamProbe — the reader's own query parameter, and what the server says it did with it.
 *
 * A listing ignores a parameter it does not read, which is correct and must stay: a stray
 * parameter cannot be allowed to break a page. Ignoring it in silence is the flaw (issue #107) —
 * as `acme`, `?tenant_id=beta` answers with acme's rows whether the parameter was refused or
 * another tenant merely held the same ones, and that ambiguity is exactly what a skeptical
 * reader presses on. So the server names what it did not read, and this brick shows it.
 *
 * The input is deliberately a raw `name=value` rather than a tenant control. There is no tenant
 * to pick: the tenant is read from the verified token and bound into the query server-side
 * (ADR 0002 layer 1), so a picker would advertise a capability that does not exist and imply the
 * refusal is a policy the server could relax. A box that appends a parameter of the reader's own
 * choosing implies nothing — it is the request itself, and it lets them try the attack rather
 * than watch a canned one.
 *
 * The notice is not tied to this box: it renders whatever the response reports as unread, from
 * wherever the parameter came.
 */

import { useState } from "react";

import { Button } from "./Button";
import { Icon } from "./Icon";
import { TextField } from "./forms";
import type { IgnoredParam } from "../lib/api";

const LABEL = "Extra query parameter";
const PLACEHOLDER = "tenant_id=beta";
const SEND = "Send";
const EXPLAINER =
  "Not a filter and not a tenant picker: whatever you type is appended to the next request " +
  "exactly as typed, and what you see below is what the server does with it. Try tenant_id=beta.";
const HEADING = "The server did not read every parameter this request carried:";

export function ParamProbe({
  id,
  ignored,
  onSend,
  disabled,
}: {
  id: string;
  ignored: IgnoredParam[];
  onSend: (probe: string) => void;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  return (
    <>
      <form
        className="search-row"
        onSubmit={(event) => {
          event.preventDefault();
          onSend(draft);
        }}
      >
        <TextField
          id={id}
          label={LABEL}
          value={draft}
          onChange={setDraft}
          placeholder={PLACEHOLDER}
        />
        <Button variant="primary" type="submit" disabled={disabled}>
          {SEND}
        </Button>
      </form>
      <p className="data-table-note">{EXPLAINER}</p>
      {ignored.length > 0 ? (
        <div className="notice notice-warn" role="status">
          <Icon name="x" size={16} />
          <div>
            <strong>{HEADING}</strong>
            <ul className="ignored-list">
              {ignored.map((param) => (
                <li key={param.name}>
                  <code>{param.name}</code> - {param.reason}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
