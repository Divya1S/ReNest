import { fireEvent, render, screen } from "@testing-library/react";
import React, { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import UnsavedChangesDialog from "./UnsavedChangesDialog";

function Harness({ onCancel = () => {}, onConfirm = () => {} }) {
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </button>
      <button type="button" onClick={() => setTick((value) => value + 1)}>
        Re-render parent
      </button>
      <UnsavedChangesDialog
        open={open}
        title="Discard changes?"
        // A new function every render, as real call sites pass.
        onCancel={() => {
          onCancel();
          setOpen(false);
        }}
        onConfirm={() => {
          onConfirm();
          setOpen(false);
        }}
      />
    </div>
  );
}

describe("UnsavedChangesDialog", () => {
  test("moves focus in, cycles Tab inside the dialog and closes on Escape", () => {
    const onCancel = vi.fn();
    render(<Harness onCancel={onCancel} />);
    const trigger = screen.getByRole("button", { name: "Open dialog" });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole("alertdialog", { name: "Discard changes?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const close = screen.getByRole("button", { name: "Close dialog" });
    const confirm = screen.getByRole("button", { name: /leave page/i });
    expect(confirm).toHaveFocus();

    // Last control: Tab wraps to the first.
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();
    // First control: Shift+Tab wraps to the last.
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(confirm).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  test("a parent re-render while open does not lose the element to return focus to", () => {
    // Regression: the focus effect re-ran whenever onCancel changed identity,
    // recorded the dialog's own button as "what to restore", and on close
    // focus fell to <body>.
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open dialog" });
    trigger.focus();
    fireEvent.click(trigger);
    const stay = screen.getByRole("button", { name: /stay here/i });
    stay.focus();

    fireEvent.click(screen.getByRole("button", { name: "Re-render parent" }));
    // Focus is where the person put it, not yanked back to the confirm button.
    stay.focus();
    fireEvent.click(screen.getByRole("button", { name: "Re-render parent" }));
    expect(stay).toHaveFocus();

    fireEvent.click(stay);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
