import { act, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import HotspotStage, {
  DEFAULT_BOX_SIZE,
  MIN_BOX_SIZE,
  applyResize,
  centeredDefaultBox,
  clampBox,
  defaultBoxAt,
  moveBox,
  normalizeBox,
  nudgeBox,
} from "./HotspotStage";

function insideImage(box) {
  return (
    box.x >= 0 &&
    box.y >= 0 &&
    box.width > 0 &&
    box.height > 0 &&
    box.x + box.width <= 1 + 1e-9 &&
    box.y + box.height <= 1 + 1e-9
  );
}

describe("hotspot geometry", () => {
  test("a click near the right or bottom edge yields a default box that stays inside the image", () => {
    // Regression: only the origin was clamped, so a click at x=0.98 produced
    // x 0.89 + width 0.18 = 1.07 and the box hung off the photo.
    const box = defaultBoxAt({ x: 0.98, y: 0.97 });
    expect(box.width).toBe(DEFAULT_BOX_SIZE);
    expect(box.height).toBe(DEFAULT_BOX_SIZE);
    expect(box.x).toBeCloseTo(0.82, 5);
    expect(box.y).toBeCloseTo(0.82, 5);
    expect(insideImage(box)).toBe(true);
  });

  test("a click near the top-left corner is pulled inward too", () => {
    expect(defaultBoxAt({ x: 0.01, y: 0.02 })).toEqual({ x: 0, y: 0, width: 0.18, height: 0.18 });
  });

  test("every corner and edge click produces an in-bounds box", () => {
    for (const x of [0, 0.05, 0.5, 0.95, 1]) {
      for (const y of [0, 0.05, 0.5, 0.95, 1]) {
        expect(insideImage(defaultBoxAt({ x, y }))).toBe(true);
        expect(insideImage(normalizeBox({ x, y }, { x, y }))).toBe(true);
      }
    }
  });

  test("normalizeBox turns a real drag into its rectangle regardless of direction", () => {
    expect(normalizeBox({ x: 0.6, y: 0.7 }, { x: 0.2, y: 0.3 })).toEqual({ x: 0.2, y: 0.3, width: 0.4, height: 0.4 });
  });

  test("normalizeBox treats a tiny drag as a click", () => {
    const box = normalizeBox({ x: 0.95, y: 0.5 }, { x: 0.96, y: 0.52 });
    expect(box.width).toBe(DEFAULT_BOX_SIZE);
    expect(insideImage(box)).toBe(true);
  });

  test("clampBox shrinks an oversized box and moves it inside", () => {
    expect(clampBox({ x: 0.9, y: -0.2, width: 1.4, height: 0.5 })).toEqual({ x: 0, y: 0, width: 1, height: 0.5 });
  });

  test("moveBox stops at the edges without changing the size", () => {
    const box = { x: 0.7, y: 0.1, width: 0.2, height: 0.3 };
    expect(moveBox(box, 0.5, -0.5)).toEqual({ x: 0.8, y: 0, width: 0.2, height: 0.3 });
  });

  test("applyResize keeps the opposite corner fixed and enforces the minimum size", () => {
    const box = { x: 0.2, y: 0.2, width: 0.3, height: 0.3 };
    expect(applyResize(box, "se", { x: 0.9, y: 0.6 })).toEqual({ x: 0.2, y: 0.2, width: 0.7, height: 0.4 });
    const collapsed = applyResize(box, "nw", { x: 0.49, y: 0.49 });
    expect(collapsed.width).toBeCloseTo(MIN_BOX_SIZE, 5);
    expect(collapsed.height).toBeCloseTo(MIN_BOX_SIZE, 5);
    expect(collapsed.x + collapsed.width).toBeCloseTo(0.5, 5);
    expect(insideImage(applyResize(box, "se", { x: 5, y: 5 }))).toBe(true);
  });

  test("nudgeBox moves by one step, resizes with the modifier and never leaves the image", () => {
    const box = { x: 0.5, y: 0.5, width: 0.2, height: 0.2 };
    expect(nudgeBox(box, "ArrowRight")).toEqual({ x: 0.51, y: 0.5, width: 0.2, height: 0.2 });
    expect(nudgeBox(box, "ArrowUp")).toEqual({ x: 0.5, y: 0.49, width: 0.2, height: 0.2 });
    expect(nudgeBox(box, "ArrowDown", { resize: true })).toEqual({ x: 0.5, y: 0.5, width: 0.2, height: 0.21 });
    expect(nudgeBox(box, "ArrowLeft", { resize: true })).toEqual({ x: 0.5, y: 0.5, width: 0.19, height: 0.2 });
    expect(nudgeBox({ ...box, x: 0.8 }, "ArrowRight")).toEqual({ x: 0.8, y: 0.5, width: 0.2, height: 0.2 });
    expect(nudgeBox({ ...box, x: 0.8 }, "ArrowRight", { resize: true }).width).toBe(0.2);
    expect(nudgeBox(box, "a")).toBeNull();
  });

  test("centeredDefaultBox is centred and staggers repeats", () => {
    expect(centeredDefaultBox()).toEqual({ x: 0.41, y: 0.41, width: 0.18, height: 0.18 });
    expect(centeredDefaultBox(1)).toEqual({ x: 0.45, y: 0.45, width: 0.18, height: 0.18 });
    expect(insideImage(centeredDefaultBox(4))).toBe(true);
  });
});

const IMAGE = { id: 1, image_url: "/media/room.jpg", position: 1 };
const LAMP = { id: 11, title: "Desk Lamp", hotspot_box: { x: 0.1, y: 0.1, width: 0.2, height: 0.2 } };

function boxStyle(element) {
  return { left: element.style.left, top: element.style.top, width: element.style.width, height: element.style.height };
}

describe("HotspotStage", () => {
  beforeEach(() => {
    // jsdom has no layout: give the stage a 1000 x 1000 box at the origin so
    // client coordinates map to fractions by dividing by 1000.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 1000, bottom: 1000, width: 1000, height: 1000, x: 0, y: 0, toJSON() {},
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test("clicking near the bottom-right corner creates a box that fits inside the photo", () => {
    const onCreate = vi.fn();
    render(<HotspotStage image={IMAGE} hotspots={[]} onCreateHotspot={onCreate} />);
    const stage = screen.getByRole("group", { name: /room photo with item hotspots/i });

    fireEvent.pointerDown(stage, { clientX: 980, clientY: 970, pointerId: 1 });
    fireEvent.pointerUp(stage, { clientX: 980, clientY: 970, pointerId: 1 });

    expect(onCreate).toHaveBeenCalledTimes(1);
    const box = onCreate.mock.calls[0][0];
    expect(box.x + box.width).toBeLessThanOrEqual(1);
    expect(box.y + box.height).toBeLessThanOrEqual(1);
    expect(box).toEqual({ x: 0.82, y: 0.82, width: 0.18, height: 0.18 });
  });

  test("keeps the dragged position on screen until the save has landed", async () => {
    // Regression: the preview was cleared on pointer-up, so the box jumped
    // back to its old place for the duration of the PATCH and reload.
    let finishSave;
    const onUpdate = vi.fn(() => new Promise((resolve) => { finishSave = resolve; }));
    const onSelect = vi.fn();
    const { rerender } = render(
      <HotspotStage image={IMAGE} hotspots={[LAMP]} selectedHotspotId={11} onUpdateHotspot={onUpdate} onSelectHotspot={onSelect} />,
    );
    const stage = screen.getByRole("group", { name: /room photo with item hotspots/i });
    const hotspot = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

    fireEvent.pointerDown(hotspot, { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(stage, { clientX: 500, clientY: 400, pointerId: 1 });
    fireEvent.pointerUp(stage, { clientX: 500, clientY: 400, pointerId: 1 });
    await act(async () => {});

    const moved = { x: 0.4, y: 0.3, width: 0.2, height: 0.2 };
    expect(onUpdate).toHaveBeenCalledWith(11, moved);
    expect(onSelect).toHaveBeenCalledWith(11);
    // Save still in flight, props still carry the old geometry: no snap back.
    expect(boxStyle(hotspot)).toEqual({ left: "40%", top: "30%", width: "20%", height: "20%" });

    rerender(
      <HotspotStage image={IMAGE} hotspots={[{ ...LAMP, hotspot_box: moved }]} selectedHotspotId={11} onUpdateHotspot={onUpdate} onSelectHotspot={onSelect} />,
    );
    await act(async () => { finishSave(); });

    expect(boxStyle(hotspot)).toEqual({ left: "40%", top: "30%", width: "20%", height: "20%" });
  });

  test("a failed save drops the preview so the box returns to its saved place", async () => {
    const onUpdate = vi.fn(() => Promise.reject(new Error("offline")));
    render(<HotspotStage image={IMAGE} hotspots={[LAMP]} selectedHotspotId={11} onUpdateHotspot={onUpdate} />);
    const stage = screen.getByRole("group", { name: /room photo with item hotspots/i });
    const hotspot = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

    fireEvent.pointerDown(hotspot, { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(stage, { clientX: 500, clientY: 400, pointerId: 1 });
    fireEvent.pointerUp(stage, { clientX: 500, clientY: 400, pointerId: 1 });
    await act(async () => {});
    await act(async () => {});

    expect(boxStyle(hotspot)).toEqual({ left: "10%", top: "10%", width: "20%", height: "20%" });
  });

  test("a click without movement selects and does not save", () => {
    const onUpdate = vi.fn();
    const onSelect = vi.fn();
    render(<HotspotStage image={IMAGE} hotspots={[LAMP]} onUpdateHotspot={onUpdate} onSelectHotspot={onSelect} />);
    const hotspot = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

    fireEvent.pointerDown(hotspot, { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerUp(hotspot, { clientX: 200, clientY: 200, pointerId: 1 });

    expect(onSelect).toHaveBeenCalledWith(11);
    expect(onUpdate).not.toHaveBeenCalled();
  });

  test("a cancelled pointer abandons the drag instead of saving it", () => {
    const onUpdate = vi.fn();
    render(<HotspotStage image={IMAGE} hotspots={[LAMP]} selectedHotspotId={11} onUpdateHotspot={onUpdate} />);
    const stage = screen.getByRole("group", { name: /room photo with item hotspots/i });
    const hotspot = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

    fireEvent.pointerDown(hotspot, { clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(stage, { clientX: 600, clientY: 600, pointerId: 1 });
    fireEvent.pointerCancel(stage, { pointerId: 1 });

    expect(onUpdate).not.toHaveBeenCalled();
    expect(boxStyle(hotspot)).toEqual({ left: "10%", top: "10%", width: "20%", height: "20%" });
  });

  describe("keyboard path", () => {
    test("Add box creates a centred default box without starting a pointer draw", () => {
      const onCreate = vi.fn();
      render(<HotspotStage image={IMAGE} hotspots={[]} onCreateHotspot={onCreate} />);
      const addBox = screen.getByRole("button", { name: "Add box" });

      fireEvent.pointerDown(addBox, { clientX: 900, clientY: 40, pointerId: 1 });
      fireEvent.pointerUp(addBox, { clientX: 900, clientY: 40, pointerId: 1 });
      fireEvent.click(addBox);

      expect(onCreate).toHaveBeenCalledTimes(1);
      expect(onCreate).toHaveBeenCalledWith({ x: 0.41, y: 0.41, width: 0.18, height: 0.18 });
    });

    test("Add box is not offered without a photo or when the stage is disabled", () => {
      const { rerender } = render(<HotspotStage image={null} hotspots={[]} />);
      expect(screen.queryByRole("button", { name: "Add box" })).not.toBeInTheDocument();
      rerender(<HotspotStage image={IMAGE} hotspots={[]} disabled />);
      expect(screen.queryByRole("button", { name: "Add box" })).not.toBeInTheDocument();
    });

    test("boxes are focusable, named, describe their keys and report selection", () => {
      render(<HotspotStage image={IMAGE} hotspots={[LAMP, { ...LAMP, id: 12, title: "" }]} selectedHotspotId={11} />);
      const lamp = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });
      expect(lamp).toHaveAttribute("tabindex", "0");
      expect(lamp).toHaveAttribute("aria-pressed", "true");
      expect(lamp).toHaveAccessibleDescription(/arrow keys to move it and shift plus the arrow keys to resize/i);
      expect(screen.getByRole("button", { name: "Hotspot: Untitled item" })).toHaveAttribute("aria-pressed", "false");
    });

    test("Enter and Space select the focused box", () => {
      const onSelect = vi.fn();
      render(<HotspotStage image={IMAGE} hotspots={[LAMP]} onSelectHotspot={onSelect} />);
      const lamp = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

      fireEvent.keyDown(lamp, { key: "Enter" });
      fireEvent.keyDown(lamp, { key: " " });

      expect(onSelect).toHaveBeenCalledTimes(2);
      expect(onSelect).toHaveBeenCalledWith(11);
    });

    test("arrow keys nudge the selected box and save once after the keys go quiet", async () => {
      vi.useFakeTimers();
      const onUpdate = vi.fn(() => Promise.resolve());
      render(<HotspotStage image={IMAGE} hotspots={[LAMP]} selectedHotspotId={11} onUpdateHotspot={onUpdate} />);
      const lamp = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

      fireEvent.keyDown(lamp, { key: "ArrowRight" });
      fireEvent.keyDown(lamp, { key: "ArrowRight" });
      fireEvent.keyDown(lamp, { key: "ArrowDown" });

      expect(boxStyle(lamp)).toEqual({ left: "12%", top: "11%", width: "20%", height: "20%" });
      expect(onUpdate).not.toHaveBeenCalled();

      await act(async () => {
        vi.advanceTimersByTime(600);
      });

      expect(onUpdate).toHaveBeenCalledTimes(1);
      expect(onUpdate).toHaveBeenCalledWith(11, { x: 0.12, y: 0.11, width: 0.2, height: 0.2 });
    });

    test("Shift plus arrow keys resize, and leaving the box saves immediately", async () => {
      const onUpdate = vi.fn(() => Promise.resolve());
      render(<HotspotStage image={IMAGE} hotspots={[LAMP]} selectedHotspotId={11} onUpdateHotspot={onUpdate} />);
      const lamp = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

      fireEvent.keyDown(lamp, { key: "ArrowRight", shiftKey: true });
      fireEvent.keyDown(lamp, { key: "ArrowUp", shiftKey: true });
      fireEvent.blur(lamp);
      await act(async () => {});

      expect(onUpdate).toHaveBeenCalledTimes(1);
      expect(onUpdate).toHaveBeenCalledWith(11, { x: 0.1, y: 0.1, width: 0.21, height: 0.19 });
    });

    test("arrow keys do nothing to a box that is not selected", () => {
      const onUpdate = vi.fn();
      render(<HotspotStage image={IMAGE} hotspots={[LAMP]} selectedHotspotId={null} onUpdateHotspot={onUpdate} />);
      const lamp = screen.getByRole("button", { name: "Hotspot: Desk Lamp" });

      fireEvent.keyDown(lamp, { key: "ArrowRight" });
      fireEvent.blur(lamp);

      expect(onUpdate).not.toHaveBeenCalled();
      expect(boxStyle(lamp)).toEqual({ left: "10%", top: "10%", width: "20%", height: "20%" });
    });
  });
});
