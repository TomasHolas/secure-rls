/**
 * ParamProbe fixtures: what the reader sends, and that the server's report is shown verbatim.
 *
 * The wording is the deliverable here (issue #107), so the assertions are about the sentence a
 * reader reads: the parameter is named, the server's own reason is repeated rather than
 * paraphrased, and nothing at all is claimed when nothing was ignored.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ParamProbe } from "./ParamProbe";
import { expectOneControlHeight } from "../test/styles";

afterEach(cleanup);

const UNKNOWN_REASON = "not a parameter this listing reads; it reads name, sort, tenant_id";
const IGNORED = [
  { name: "tenant", reason: UNKNOWN_REASON },
  { name: "db_path", reason: "not a parameter this listing reads; it reads name, sort" },
];

describe("ParamProbe", () => {
  it("names every parameter the server did not read, with the server's own reason", () => {
    const view = render(<ParamProbe id="probe" ignored={IGNORED} onSend={vi.fn()} />);

    const notice = view.container.querySelector(".notice-warn");
    expect(notice?.textContent).toContain("did not read every parameter");
    expect(notice?.textContent).toContain("tenant");
    expect(notice?.textContent).toContain(UNKNOWN_REASON);
    expect(notice?.textContent).toContain("db_path");
    expect(view.container.querySelectorAll(".ignored-list li")).toHaveLength(2);
  });

  it("says nothing when the server read every parameter it was sent", () => {
    const view = render(<ParamProbe id="probe" ignored={[]} onSend={vi.fn()} />);

    expect(view.container.querySelector(".notice")).toBeNull();
  });

  it("sends what the reader typed, unaltered", () => {
    const onSend = vi.fn();
    render(<ParamProbe id="probe" ignored={[]} onSend={onSend} />);

    fireEvent.change(screen.getByLabelText("Extra query parameter"), {
      target: { value: "tenant_id=beta" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("tenant_id=beta");
  });

  it("puts its send control on the height and baseline of its box", () => {
    const view = render(<ParamProbe id="probe" ignored={[]} onSend={vi.fn()} />);

    expectOneControlHeight(view.container.querySelector(".search-row"), 2);
  });

  it("claims only what is still true of a listing that filters by tenant", () => {
    render(<ParamProbe id="probe" ignored={[]} onSend={vi.fn()} />);

    const explainer = screen.getByText(/whatever you type is appended/i).textContent ?? "";
    expect(explainer).toContain("tenant_id IS a filter here");
    expect(explainer).toContain("comes from your verified token");
    expect(explainer).not.toMatch(/no request can name a tenant/i);
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
