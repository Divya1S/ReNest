import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ListingFormPage from "./ListingFormPage";

function jsonResponse(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body });
}

function mockApi(overrides = {}) {
  return vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const key = `${method} ${url}`;
    for (const [pattern, body] of Object.entries(overrides)) {
      if (key.includes(pattern)) {
        return jsonResponse(typeof body === "function" ? body(opts) : body);
      }
    }
    if (key.includes("GET /api/listings/pricing-hint")) {
      return jsonResponse({ sample_size: 0, suggestion: "" });
    }
    return jsonResponse({});
  });
}

function renderCreateForm() {
  const router = createMemoryRouter(
    [
      { path: "/listings/new", element: <ListingFormPage /> },
      { path: "/listings/:listingId", element: <div>DETAIL STUB</div> },
    ],
    { initialEntries: ["/listings/new"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

const FUTURE_DEADLINE = (() => {
  const d = new Date(Date.now() + 5 * 86400000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
})();

function fillRequiredFields() {
  fireEvent.change(screen.getByPlaceholderText(/dorm fan, storage bin/i), {
    target: { value: "Sturdy desk lamp" },
  });
  fireEvent.change(screen.getByPlaceholderText(/describe condition/i), {
    target: { value: "Bright LED lamp, barely used, includes bulb and cable." },
  });
  fireEvent.change(screen.getByPlaceholderText(/dorm a lobby/i), {
    target: { value: "North Hall front desk" },
  });
  fireEvent.change(document.querySelector('input[type="datetime-local"]'), {
    target: { value: FUTURE_DEADLINE },
  });
}

describe("ListingFormPage (create)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("submitting an empty form shows field errors and sends nothing", async () => {
    const spy = mockApi();
    renderCreateForm();

    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => {
      expect(screen.getByText("Title is required.")).toBeInTheDocument();
      expect(screen.getByText("Description is required.")).toBeInTheDocument();
      expect(screen.getByText("Pickup zone is required.")).toBeInTheDocument();
      expect(screen.getByText("Move-out deadline is required.")).toBeInTheDocument();
    });

    const postCalls = spy.mock.calls.filter(
      ([url, opts]) => String(url).endsWith("/api/listings") && opts?.method === "POST",
    );
    expect(postCalls).toHaveLength(0);
  });

  test("low-cost pricing requires a price greater than zero", async () => {
    mockApi();
    renderCreateForm();
    fillRequiredFields();

    fireEvent.change(screen.getByDisplayValue("Free"), { target: { value: "low_cost" } });
    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => {
      expect(screen.getByText(/price greater than \$0/i)).toBeInTheDocument();
    });
  });

  test("valid form POSTs the listing and navigates to its detail page", async () => {
    const spy = mockApi({
      "POST /api/listings": { id: 55, title: "Sturdy desk lamp" },
    });
    renderCreateForm();
    fillRequiredFields();

    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => {
      const createCall = spy.mock.calls.find(
        ([url, opts]) => String(url).endsWith("/api/listings") && opts?.method === "POST",
      );
      expect(createCall).toBeDefined();
      const payload = JSON.parse(createCall[1].body);
      expect(payload).toMatchObject({
        title: "Sturdy desk lamp",
        pickup_zone: "North Hall front desk",
        price_type: "free",
      });
    });

    await waitFor(() => {
      expect(screen.getByText("DETAIL STUB")).toBeInTheDocument();
    });
  });

  test("field errors are announced, tied to their inputs and focus the first one", async () => {
    // Regression: errors were bare paragraphs. Nothing marked the input
    // invalid or pointed assistive tech at the message.
    mockApi();
    renderCreateForm();

    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    const title = screen.getByLabelText("Item Title");
    await waitFor(() => expect(title).toHaveAttribute("aria-invalid", "true"));
    expect(title).toHaveAccessibleDescription("Title is required.");
    expect(title).toHaveFocus();
    const alerts = screen.getAllByRole("alert").map((node) => node.textContent);
    expect(alerts).toEqual(
      expect.arrayContaining([
        "Title is required.",
        "Description is required.",
        "Pickup zone is required.",
        "Move-out deadline is required.",
      ]),
    );
    expect(screen.getByLabelText("Description")).toHaveAccessibleDescription("Description is required.");
    expect(screen.getByLabelText("Pickup Zone")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Available Until")).toHaveAttribute("aria-invalid", "true");
    // Fields with nothing wrong carry no invalid state.
    expect(screen.getByLabelText("Category")).not.toHaveAttribute("aria-invalid");

    fireEvent.change(title, { target: { value: "Sturdy desk lamp" } });
    expect(title).not.toHaveAttribute("aria-invalid");
    expect(title).not.toHaveAccessibleDescription();
    expect(screen.queryByText("Title is required.")).not.toBeInTheDocument();
  });

  test("the label names the field without swallowing its error text", async () => {
    mockApi();
    renderCreateForm();
    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => expect(screen.getByText("Title is required.")).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "Item Title" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Pickup Zone" })).toBeInTheDocument();
  });

  describe("server validation errors", () => {
    function rejectCreate(payload) {
      return vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
        const method = (opts.method || "GET").toUpperCase();
        if (method === "POST" && String(url).endsWith("/api/listings")) return jsonResponse(payload, 400);
        if (String(url).includes("/pricing-hint")) return jsonResponse({ sample_size: 0, suggestion: "" });
        return jsonResponse({});
      });
    }

    test("are shown next to the fields they are about", async () => {
      // Regression: the server's per-field messages were flattened into one
      // sentence under the form, with no way to tell which field each meant.
      rejectCreate({
        title: ["A listing with this title already exists."],
        pickup_zone: ["Pick a zone on your campus."],
        estimated_retail_value: ["Ensure this value is less than or equal to 10000."],
        non_field_errors: ["Your campus is not accepting listings yet."],
      });
      renderCreateForm();
      fillRequiredFields();

      fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

      const title = screen.getByLabelText("Item Title");
      await waitFor(() => expect(title).toHaveAttribute("aria-invalid", "true"));
      expect(title).toHaveAccessibleDescription("A listing with this title already exists.");
      expect(title).toHaveFocus();
      expect(screen.getByLabelText("Pickup Zone")).toHaveAccessibleDescription("Pick a zone on your campus.");
      expect(screen.getByLabelText("Estimated Retail Value")).toHaveAccessibleDescription(
        "Ensure this value is less than or equal to 10000.",
      );
      // The message that belongs to no field stays in the summary.
      expect(
        screen.getByText("Some fields need attention. See the messages next to them. Your campus is not accepting listings yet."),
      ).toBeInTheDocument();
      // Still on the form: nothing navigated away.
      expect(screen.queryByText("DETAIL STUB")).not.toBeInTheDocument();
    });

    test("stay until the field is edited, not merely left", async () => {
      rejectCreate({ title: ["A listing with this title already exists."] });
      renderCreateForm();
      fillRequiredFields();
      fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

      const title = screen.getByLabelText("Item Title");
      await waitFor(() => expect(title).toHaveAttribute("aria-invalid", "true"));

      // Client-side validation passes for this value; the server's verdict must survive a blur.
      fireEvent.blur(title);
      expect(screen.getByText("A listing with this title already exists.")).toBeInTheDocument();

      fireEvent.change(title, { target: { value: "Sturdy desk lamp (brass)" } });
      expect(screen.queryByText("A listing with this title already exists.")).not.toBeInTheDocument();
    });

    test("nested error objects never render as [object Object]", async () => {
      rejectCreate({
        title: [{ message: "Too generic.", code: "generic" }],
        gallery: [{ image: ["Unsupported image type."] }],
      });
      renderCreateForm();
      fillRequiredFields();
      fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

      await waitFor(() => expect(screen.getByLabelText("Item Title")).toHaveAttribute("aria-invalid", "true"));
      expect(document.body.textContent).not.toContain("[object Object]");
      expect(screen.getByText(/unsupported image type\./i)).toBeInTheDocument();
    });

    test("a failure with no field information falls back to the plain message", async () => {
      rejectCreate({ detail: "Verify your email before posting." });
      renderCreateForm();
      fillRequiredFields();
      fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

      expect(await screen.findByText("Verify your email before posting.")).toBeInTheDocument();
      expect(screen.getByLabelText("Item Title")).not.toHaveAttribute("aria-invalid");
    });
  });

  test("falls back to a multipart upload when direct upload is unavailable", async () => {
    // jsdom has no object URLs; the form previews the chosen cover photo.
    URL.createObjectURL = vi.fn(() => "blob:preview");
    URL.revokeObjectURL = vi.fn();
    const spy = vi.spyOn(global, "fetch").mockImplementation((url, opts = {}) => {
      const method = (opts.method || "GET").toUpperCase();
      if (method === "POST" && String(url).endsWith("/upload-url")) {
        return jsonResponse({ detail: "Direct upload is not configured." }, 501);
      }
      if (String(url).includes("/pricing-hint")) return jsonResponse({ sample_size: 0, suggestion: "" });
      return jsonResponse({ id: 55, title: "Sturdy desk lamp" });
    });
    renderCreateForm();
    fillRequiredFields();
    const file = new File(["lamp"], "lamp.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText(/add cover photo/i), { target: { files: [file] } });

    fireEvent.click(screen.getByRole("button", { name: /create listing/i }));

    await waitFor(() => expect(screen.getByText("DETAIL STUB")).toBeInTheDocument());
    const multipart = spy.mock.calls.find(
      ([url, opts]) => String(url).endsWith("/api/listings/55") && opts?.method === "PATCH" && opts.body instanceof FormData,
    );
    expect(multipart).toBeDefined();
    expect(multipart[1].body.get("image")).toBe(file);
    delete URL.createObjectURL;
    delete URL.revokeObjectURL;
  });
});

describe("ListingFormPage (edit)", () => {
  const LISTING = {
    id: 7,
    title: "Mini Fridge",
    description: "Cold and compact, cleaned and ready to go.",
    category: "comfort",
    condition: "good",
    price_type: "low_cost",
    price_amount: "45.00",
    estimated_retail_value: "120.00",
    pickup_zone: "North Hall lobby",
    building: "North Hall",
    available_until: new Date(Date.now() + 3 * 86400000).toISOString(),
    image_url: null,
    gallery: [],
  };

  function renderEditForm() {
    const router = createMemoryRouter(
      [
        { path: "/listings/:listingId/edit", element: <ListingFormPage /> },
        { path: "/my-listings", element: <div>MY LISTINGS STUB</div> },
      ],
      { initialEntries: ["/listings/7/edit"] },
    );
    render(<RouterProvider router={router} />);
    return router;
  }

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("a failed load shows a retry instead of an empty form", async () => {
    // Regression: the load error was printed under a blank form, which looked
    // like an empty listing ready to be saved over the real one.
    let healthy = false;
    vi.spyOn(global, "fetch").mockImplementation((url) => {
      if (String(url).includes("/pricing-hint")) return jsonResponse({ sample_size: 0, suggestion: "" });
      return healthy ? jsonResponse(LISTING) : jsonResponse({ detail: "Internal server error." }, 500);
    });
    renderEditForm();

    expect(await screen.findByRole("heading", { name: /could not be loaded/i })).toBeInTheDocument();
    expect(screen.getByText("Internal server error.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save changes/i })).not.toBeInTheDocument();

    healthy = true;
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));

    expect(await screen.findByLabelText("Item Title")).toHaveValue("Mini Fridge");
    expect(screen.getByRole("button", { name: /save changes/i })).toBeInTheDocument();
  });

  test("a 404 reads as not found and offers no retry", async () => {
    vi.spyOn(global, "fetch").mockImplementation(() => jsonResponse({ detail: "Not found." }, 404));
    renderEditForm();

    expect(await screen.findByRole("heading", { name: /listing not found/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to my listings/i })).toHaveAttribute("href", "/my-listings");
  });
});
