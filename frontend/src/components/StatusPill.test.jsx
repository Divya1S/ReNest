import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, test } from "vitest";

import StatusPill from "./StatusPill";

describe("StatusPill", () => {
  test.each([
    ["available", "Available"],
    ["reserved", "Reserved"],
    ["picked_up", "Picked Up"],
    ["expired", "Expired"],
    ["donated", "Donated"],
  ])("labels the listing status %s", (status, label) => {
    render(<StatusPill status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  test.each([
    ["requested", "Requested"],
    ["confirmed", "Confirmed"],
    ["completed", "Completed"],
    ["cancelled", "Cancelled"],
    ["expired_unresolved", "Overdue"],
  ])("labels the reservation status %s instead of printing the raw value", (status, label) => {
    render(<StatusPill status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText(status)).not.toBeInTheDocument();
  });
});
