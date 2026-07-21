import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { apiFetch } from "../lib/api";

import AssistantPage from "./AssistantPage";

vi.mock("../lib/api", () => ({
  apiFetch: vi.fn(),
}));

describe("AssistantPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test("loads the shared concierge thread on mount and shows history", async () => {
    apiFetch.mockResolvedValue({
      enabled: true,
      messages: [
        { id: 1, role: "user", content: "any lamps?", used_tools: [], meta: {} },
        { id: 2, role: "assistant", content: "Found a Clip-on lamp.", used_tools: [], meta: {} },
      ],
    });
    render(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: /your move-out assistant/i })).toBeInTheDocument();
    expect(await screen.findByText("Found a Clip-on lamp.")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith("/concierge/history/");
  });

  test("sends a message through the same concierge endpoint as the widget", async () => {
    apiFetch.mockImplementation((path) => {
      if (path === "/concierge/chat/") {
        return Promise.resolve({
          reply: "Here's what I found.",
          used_tools: ["search_listings"],
          degraded: false,
          meta: { route: { name: "task" }, ui_blocks: [] },
        });
      }
      return Promise.resolve({ enabled: true, messages: [] });
    });
    render(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    );
    await screen.findByText(/i can search live listings/i);

    fireEvent.change(screen.getByLabelText(/message the concierge/i), {
      target: { value: "any fans?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    expect(await screen.findByText("Here's what I found.")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(
      "/concierge/chat/",
      expect.objectContaining({ method: "POST", body: { message: "any fans?" } }),
    );
  });

  test("offline concierge shows the calm notice and disables the composer", async () => {
    apiFetch.mockResolvedValue({ enabled: false, messages: [] });
    render(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/concierge is offline right now/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/message the concierge/i)).toBeDisabled();
  });

  test("clear chat resets the thread", async () => {
    apiFetch.mockImplementation((path, options = {}) => {
      if (path === "/concierge/reset/" && options.method === "POST") return Promise.resolve({});
      return Promise.resolve({
        enabled: true,
        messages: [{ id: 1, role: "assistant", content: "Earlier reply", used_tools: [], meta: {} }],
      });
    });
    render(
      <MemoryRouter>
        <AssistantPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Earlier reply")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear chat/i }));

    await waitFor(() => expect(screen.queryByText("Earlier reply")).not.toBeInTheDocument());
    expect(apiFetch).toHaveBeenCalledWith("/concierge/reset/", expect.objectContaining({ method: "POST" }));
  });
});
