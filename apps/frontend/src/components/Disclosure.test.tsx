/**
 * Disclosure fixtures: it is closed until asked, its body is absent rather than hidden while it
 * is, and the trigger says what the next click does (issue #139).
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Disclosure } from "./Disclosure";

const SHOW = "show the SQL this page ran";
const HIDE = "hide the SQL this page ran";

function show() {
  return render(
    <Disclosure show={SHOW} hide={HIDE}>
      <p>SELECT 1</p>
    </Disclosure>,
  );
}

afterEach(cleanup);

describe("Disclosure", () => {
  it("starts closed, with the body out of the document rather than hidden in it", () => {
    show();

    expect(screen.queryByText("SELECT 1")).toBeNull();
    expect(screen.getByRole("button", { name: SHOW })).toHaveProperty("ariaExpanded", "false");
  });

  it("opens on the click and closes again, swapping what the trigger promises", () => {
    show();

    fireEvent.click(screen.getByRole("button", { name: SHOW }));

    expect(screen.getByText("SELECT 1")).toBeTruthy();
    expect(screen.getByRole("button", { name: HIDE })).toHaveProperty("ariaExpanded", "true");

    fireEvent.click(screen.getByRole("button", { name: HIDE }));

    expect(screen.queryByText("SELECT 1")).toBeNull();
    expect(screen.getByRole("button", { name: SHOW })).toBeTruthy();
  });
});
