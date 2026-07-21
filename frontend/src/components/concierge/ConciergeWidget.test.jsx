import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiFetch } from "../../lib/api";

import ConciergeWidget from "./ConciergeWidget";

vi.mock("../../lib/api", () => ({
  apiFetch: vi.fn(),
}));

function mockHistory({ enabled = true, messages = [] } = {}) {
  apiFetch.mockImplementation((path) => {
    if (path === "/concierge/history/") {
      return Promise.resolve({ enabled, messages });
    }
    return Promise.reject(new Error(`unexpected call: ${path}`));
  });
}

function mockChat(reply) {
  apiFetch.mockImplementation((path) => {
    if (path === "/concierge/chat/") return Promise.resolve(reply);
    return Promise.resolve({ enabled: true, messages: [] });
  });
}

async function openWidget() {
  render(
    <MemoryRouter>
      <ConciergeWidget />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: /open nest concierge/i }));
  await waitFor(() => expect(screen.getByRole("dialog", { name: /nest concierge/i })).toBeInTheDocument());
}

function sendMessage(text) {
  fireEvent.change(screen.getByLabelText(/message the concierge/i), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /send message/i }));
}

function sseStream() {
  let controller;
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(c) {
      controller = c;
    },
  });
  return {
    response: { ok: true, status: 200, body: stream },
    push: (obj) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(obj)}\n\n`)),
    close: () => controller.close(),
  };
}

describe("ConciergeWidget", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("launcher opens the panel, loads history, and shows intro suggestions", async () => {
    mockHistory();
    await openWidget();

    await waitFor(() =>
      expect(screen.getByText(/i can search live listings/i)).toBeInTheDocument(),
    );
    expect(apiFetch).toHaveBeenCalledWith("/concierge/history/");
    expect(screen.getByRole("button", { name: "How do handoffs work?" })).toBeInTheDocument();
  });

  test("sends a message and renders the reply with tool + plan transparency", async () => {
    mockHistory();
    await openWidget();
    mockChat({
      reply: "Found a Clip-on lamp in Maple Hall lobby.",
      used_tools: ["search_listings"],
      degraded: false,
      meta: {
        route: { name: "task" },
        plan: { steps: [{ tool: "search_listings", why: "find lamps" }] },
        reflection: { passed: true },
        revised: false,
        ui_blocks: [],
      },
    });

    sendMessage("any lamps?");

    expect(await screen.findByText("any lamps?")).toBeInTheDocument();
    expect(await screen.findByText(/clip-on lamp/i)).toBeInTheDocument();
    expect(screen.getByText(/checked live listings · planned 1 step · self-checked/i)).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(
      "/concierge/chat/",
      expect.objectContaining({ method: "POST", body: { message: "any lamps?" }, timeout: 60_000 }),
    );
  });

  test("renders generative listing cards from server-hydrated ui_blocks", async () => {
    mockHistory();
    await openWidget();
    mockChat({
      reply: "Here's what I found.",
      used_tools: ["search_listings", "show_listing_cards"],
      degraded: false,
      meta: {
        route: { name: "task" },
        ui_blocks: [
          {
            type: "listing_cards",
            props: {
              listings: [
                { id: 7, title: "Mini fan", price_type: "free", pickup_zone: "Maple Hall lobby", thumb: null },
              ],
            },
          },
          { type: "totally_unknown_block", props: {} },
        ],
      },
    });

    sendMessage("any fans?");

    const card = await screen.findByRole("link", { name: /mini fan/i });
    expect(card).toHaveAttribute("href", "/listings/7");
    expect(screen.getByText("Free")).toBeInTheDocument();
    // Unknown block types are skipped, never crash the widget.
    expect(screen.getByRole("dialog", { name: /nest concierge/i })).toBeInTheDocument();
  });

  test("follow-up suggestion chips send when tapped", async () => {
    mockHistory();
    await openWidget();
    mockChat({
      reply: "You have one reservation.",
      used_tools: [],
      degraded: false,
      meta: {
        route: { name: "task" },
        ui_blocks: [{ type: "suggestions", props: { suggestions: ["When is my pickup?"] } }],
      },
    });

    sendMessage("my reservations?");
    const chip = await screen.findByRole("button", { name: "When is my pickup?" });

    mockChat({ reply: "Friday 3-5pm.", used_tools: [], degraded: false, meta: { ui_blocks: [] } });
    fireEvent.click(chip);

    expect(await screen.findByText("Friday 3-5pm.")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(
      "/concierge/chat/",
      expect.objectContaining({ body: { message: "When is my pickup?" } }),
    );
  });

  test("cached replies show the instant-answer caption", async () => {
    mockHistory();
    await openWidget();
    mockChat({
      reply: "Handoffs complete with a 6-digit PIN.",
      used_tools: [],
      degraded: false,
      meta: { route: { name: "chat" }, cached: true, ui_blocks: [] },
    });

    sendMessage("how do handoffs work");

    expect(await screen.findByText(/instant answer/i)).toBeInTheDocument();
  });

  test("a failed chat call renders an inline error and the widget survives", async () => {
    mockHistory();
    await openWidget();
    apiFetch.mockRejectedValue(new Error("network down"));

    sendMessage("hello?");

    expect(await screen.findByText(/couldn't get a reply just now/i)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: /nest concierge/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/message the concierge/i)).not.toBeDisabled();
  });

  test("offline concierge shows a calm notice and disables input", async () => {
    mockHistory({ enabled: false });
    await openWidget();

    expect(await screen.findByText(/concierge is offline right now/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/message the concierge/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
  });

  test("streaming send shows live phases, then the reply with feedback thumbs", async () => {
    mockHistory();
    await openWidget();

    const sse = sseStream();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sse.response));

    sendMessage("check my listings please");
    sse.push({ type: "phase", phase: "planning" });
    expect(await screen.findByText(/planning an approach/i)).toBeInTheDocument();

    sse.push({ type: "phase", phase: "tool", tool: "search_listings" });
    expect(await screen.findByText(/checking live listings/i)).toBeInTheDocument();

    sse.push({
      type: "done",
      reply: "All caught up.",
      used_tools: [],
      degraded: false,
      meta: { route: { name: "task" }, ui_blocks: [] },
      message_id: 42,
    });
    sse.close();

    expect(await screen.findByText("All caught up.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Helpful" })).toBeInTheDocument();
    // The streaming path answered — the JSON fallback was never used.
    expect(apiFetch).not.toHaveBeenCalledWith("/concierge/chat/", expect.anything());
  });

  test("stream failure falls back to the JSON endpoint transparently", async () => {
    mockHistory();
    await openWidget();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    mockChat({ reply: "Fallback worked.", used_tools: [], degraded: false, meta: { ui_blocks: [] } });

    sendMessage("hello");

    expect(await screen.findByText("Fallback worked.")).toBeInTheDocument();
  });

  test("thumbs feedback posts the rating and toggles pressed state", async () => {
    mockHistory({
      messages: [{ id: 7, role: "assistant", content: "Earlier reply", used_tools: [], meta: {}, rating: null }],
    });
    await openWidget();
    await screen.findByText("Earlier reply");
    apiFetch.mockResolvedValue({});

    fireEvent.click(screen.getByRole("button", { name: "Helpful" }));

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        "/concierge/messages/7/feedback/",
        expect.objectContaining({ method: "POST", body: { rating: "up" } }),
      ),
    );
    expect(screen.getByRole("button", { name: "Helpful" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Helpful" }));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        "/concierge/messages/7/feedback/",
        expect.objectContaining({ body: { rating: "clear" } }),
      ),
    );
  });

  test("path suggestions render as navigation chips, plain ones as send chips", async () => {
    mockHistory({
      messages: [{
        id: 9, role: "assistant", content: "Storage is hot right now.", used_tools: [],
        meta: { ui_blocks: [{ type: "suggestions", props: { suggestions: [
          { label: "Browse storage", path: "/browse?category=storage" },
          { label: "What about lamps?", path: null },
        ] } }] },
      }],
    });
    await openWidget();

    const linkChip = await screen.findByRole("link", { name: /browse storage/i });
    expect(linkChip).toHaveAttribute("href", "/browse?category=storage");
    expect(screen.getByRole("button", { name: "What about lamps?" })).toBeInTheDocument();
  });

  test("saved-search proposal only creates the alert when the user confirms", async () => {
    mockHistory({
      messages: [{
        id: 11, role: "assistant", content: "I can watch for that.", used_tools: [],
        meta: { ui_blocks: [{ type: "saved_search_proposal", props: {
          keyword: "mini fridge", category: "storage", price_type: "free", label: "Mini Fridge",
        } }] },
      }],
    });
    await openWidget();

    expect(await screen.findByText(/alert proposal/i)).toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalledWith("/saved-searches", expect.anything());

    apiFetch.mockResolvedValue({});
    fireEvent.click(screen.getByRole("button", { name: /create alert/i }));

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        "/saved-searches",
        expect.objectContaining({
          method: "POST",
          body: { keyword: "mini fridge", category: "storage", price_type: "free", label: "Mini Fridge" },
        }),
      ),
    );
    expect(await screen.findByText(/alert on/i)).toBeInTheDocument();
  });

  test("Tab is trapped inside the open dialog", async () => {
    mockHistory();
    await openWidget();
    const dialog = screen.getByRole("dialog", { name: /nest concierge/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");

    const focusables = dialog.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled])',
    );
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    last.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(first).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  test("show-earlier loads older scrollback without losing newer messages", async () => {
    apiFetch.mockImplementation((path) => {
      if (path === "/concierge/history/") {
        return Promise.resolve({
          enabled: true,
          has_more: true,
          messages: [{ id: 40, role: "assistant", content: "Recent reply", used_tools: [], meta: {} }],
        });
      }
      if (path === "/concierge/history/?before=40") {
        return Promise.resolve({
          enabled: true,
          has_more: false,
          messages: [{ id: 12, role: "user", content: "An older question", used_tools: [], meta: {} }],
        });
      }
      return Promise.reject(new Error(`unexpected call: ${path}`));
    });
    await openWidget();

    fireEvent.click(await screen.findByRole("button", { name: /show earlier messages/i }));

    expect(await screen.findByText("An older question")).toBeInTheDocument();
    expect(screen.getByText("Recent reply")).toBeInTheDocument();
    // has_more false → the button disappears.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /show earlier messages/i })).not.toBeInTheDocument(),
    );
  });

  test("clear conversation resets to the intro state", async () => {
    mockHistory({
      messages: [{ id: 1, role: "assistant", content: "Earlier reply", used_tools: [], meta: {} }],
    });
    await openWidget();
    expect(await screen.findByText("Earlier reply")).toBeInTheDocument();

    apiFetch.mockImplementation((path, options = {}) => {
      if (path === "/concierge/reset/" && options.method === "POST") return Promise.resolve({});
      return Promise.resolve({ enabled: true, messages: [] });
    });
    fireEvent.click(screen.getByRole("button", { name: /clear conversation/i }));

    await waitFor(() => expect(screen.queryByText("Earlier reply")).not.toBeInTheDocument());
    expect(screen.getByText(/i can search live listings/i)).toBeInTheDocument();
  });
});
