import { motion } from "framer-motion";
import { Plus } from "lucide-react";
import React, { useEffect, useId, useMemo, useRef, useState } from "react";

// ─── Pure geometry ────────────────────────────────────────────────────────────
// Every box is { x, y, width, height } in fractions of the image (0..1). These
// helpers are exported so the edge cases can be unit-tested without a DOM.

/** Side of the box a plain click (or the "Add box" control) creates. */
export const DEFAULT_BOX_SIZE = 0.18;
/** Smallest box a resize can produce. */
export const MIN_BOX_SIZE = 0.05;
/** A drag smaller than this on either axis is treated as a click. The server
 *  rejects boxes of 0.02 or less, so the threshold sits above that. */
export const MIN_DRAWN_SIZE = 0.03;
/** Distance one arrow-key press moves or resizes a box. */
export const NUDGE_STEP = 0.01;

export function clamp(v, lo = 0, hi = 1) {
  return Math.min(hi, Math.max(lo, v));
}

// Four decimals is finer than a pixel on any photo and keeps float noise
// (0.82 + 0.18 = 1.0000000000000002) from pushing a box past the edge.
function round(v) {
  return Math.round(v * 10000) / 10000;
}

/** Fit a box entirely inside the image: size first, then position. */
export function clampBox(box) {
  const width = round(clamp(box.width, MIN_DRAWN_SIZE, 1));
  const height = round(clamp(box.height, MIN_DRAWN_SIZE, 1));
  return {
    x: round(clamp(box.x, 0, 1 - width)),
    y: round(clamp(box.y, 0, 1 - height)),
    width,
    height,
  };
}

/**
 * The default box centred on a point. Near an edge the whole box shifts
 * inward: clamping only the origin left the far edge at up to 1.07.
 */
export function defaultBoxAt(point) {
  return clampBox({
    x: point.x - DEFAULT_BOX_SIZE / 2,
    y: point.y - DEFAULT_BOX_SIZE / 2,
    width: DEFAULT_BOX_SIZE,
    height: DEFAULT_BOX_SIZE,
  });
}

/** The box the keyboard "Add box" control creates. `index` staggers repeats. */
export function centeredDefaultBox(index = 0) {
  const offset = (index % 5) * 0.04;
  return defaultBoxAt({ x: 0.5 + offset, y: 0.5 + offset });
}

/** The box described by a drag from `start` to `end`, or the default for a click. */
export function normalizeBox(start, end) {
  const width = Math.abs(end.x - start.x);
  const height = Math.abs(end.y - start.y);
  if (width < MIN_DRAWN_SIZE || height < MIN_DRAWN_SIZE) {
    return defaultBoxAt(start);
  }
  return clampBox({ x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width, height });
}

/** Translate a box, stopping at the image edges without changing its size. */
export function moveBox(box, dx, dy) {
  return {
    x: round(clamp(box.x + dx, 0, 1 - box.width)),
    y: round(clamp(box.y + dy, 0, 1 - box.height)),
    width: box.width,
    height: box.height,
  };
}

/** The box that results from dragging one corner of `original` to `pos`. */
export function applyResize(original, corner, pos) {
  let { x, y, width, height } = original;
  const right = x + width;
  const bottom = y + height;

  if (corner === "se") {
    width = clamp(pos.x - x, MIN_BOX_SIZE, 1 - x);
    height = clamp(pos.y - y, MIN_BOX_SIZE, 1 - y);
  } else if (corner === "sw") {
    x = clamp(pos.x, 0, right - MIN_BOX_SIZE);
    width = right - x;
    height = clamp(pos.y - y, MIN_BOX_SIZE, 1 - y);
  } else if (corner === "ne") {
    width = clamp(pos.x - x, MIN_BOX_SIZE, 1 - x);
    y = clamp(pos.y, 0, bottom - MIN_BOX_SIZE);
    height = bottom - y;
  } else if (corner === "nw") {
    x = clamp(pos.x, 0, right - MIN_BOX_SIZE);
    y = clamp(pos.y, 0, bottom - MIN_BOX_SIZE);
    width = right - x;
    height = bottom - y;
  }
  return { x: round(x), y: round(y), width: round(width), height: round(height) };
}

const ARROW_DELTAS = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

/**
 * The box after one arrow-key press: a move, or with `resize` a change of
 * width/height anchored at the top-left corner. Returns null for other keys.
 */
export function nudgeBox(box, key, { resize = false, step = NUDGE_STEP } = {}) {
  const delta = ARROW_DELTAS[key];
  if (!delta) return null;
  const [dx, dy] = delta;
  if (!resize) return moveBox(box, dx * step, dy * step);
  return {
    x: box.x,
    y: box.y,
    width: round(clamp(box.width + dx * step, MIN_BOX_SIZE, 1 - box.x)),
    height: round(clamp(box.height + dy * step, MIN_BOX_SIZE, 1 - box.y)),
  };
}

// Corner handle descriptors: position as fraction of box (anchorX, anchorY) + CSS cursor
const HANDLES = [
  { id: "nw", ax: 0, ay: 0, cursor: "nw-resize" },
  { id: "ne", ax: 1, ay: 0, cursor: "ne-resize" },
  { id: "sw", ax: 0, ay: 1, cursor: "sw-resize" },
  { id: "se", ax: 1, ay: 1, cursor: "se-resize" },
];

// Arrow-key edits are saved once the keys go quiet, not once per press.
const KEYBOARD_COMMIT_DELAY_MS = 500;

export default function HotspotStage({
  image,
  hotspots,
  selectedHotspotId,
  onSelectHotspot,
  onCreateHotspot,
  onUpdateHotspot,
  disabled = false,
}) {
  const stageRef = useRef(null);
  const keyboardHelpId = useId();

  // Hotspot coordinates are normalised against the *image* (both AI detection
  // and manual drags), while the overlay is positioned against the *stage*.
  // Those two only agree when the stage has the photo's own aspect ratio: with
  // a fixed 4:3 box and object-cover, a 3:4 phone photo is cropped and every
  // AI-detected box lands in the wrong place. Adopt the image's ratio on load.
  const [imageRatio, setImageRatio] = useState(null);

  // Drawing state, for creating a brand-new hotspot
  const [drawStart, setDrawStart] = useState(null);
  const [draftBox, setDraftBox] = useState(null);

  // Drag state, for moving or resizing an existing hotspot
  // { type: "move"|"resize", id, corner?, startPos, originalBox, moved }
  const dragRef = useRef(null);
  // { id, box }: geometry shown instead of the saved one. Live while dragging
  // or nudging, then held until the save has landed (see commitBox).
  const [liveBox, setLiveBox] = useState(null);

  // Arrow-key edit waiting to be saved: { id, box, timer }
  const pendingKeyboardRef = useRef(null);
  const onUpdateHotspotRef = useRef(onUpdateHotspot);
  onUpdateHotspotRef.current = onUpdateHotspot;

  const resolvedHotspots = useMemo(() => hotspots || [], [hotspots]);

  useEffect(() => {
    setImageRatio(null);
  }, [image?.image_url]);

  // Save a pending arrow-key edit if the stage goes away before its timer.
  useEffect(
    () => () => {
      const pending = pendingKeyboardRef.current;
      if (!pending) return;
      clearTimeout(pending.timer);
      pendingKeyboardRef.current = null;
      onUpdateHotspotRef.current?.(pending.id, pending.box);
    },
    [],
  );

  function getPos(event) {
    const bounds = stageRef.current?.getBoundingClientRect();
    if (!bounds || !bounds.width || !bounds.height) return { x: 0, y: 0 };
    return {
      x: clamp((event.clientX - bounds.left) / bounds.width),
      y: clamp((event.clientY - bounds.top) / bounds.height),
    };
  }

  function capturePointer(event) {
    try {
      stageRef.current?.setPointerCapture?.(event.pointerId);
    } catch {
      // Capture is an optimisation (keeps the drag alive outside the stage);
      // a pointer that cannot be captured still drags inside it.
    }
  }

  /**
   * Hand a changed box to the parent and keep previewing it until the parent
   * is done. Clearing the preview on pointer-up showed the old geometry for
   * the length of the PATCH and reload, so the box visibly snapped back.
   */
  function commitBox(id, box) {
    setLiveBox({ id, box });
    Promise.resolve()
      .then(() => onUpdateHotspotRef.current?.(id, box))
      .catch(() => {
        // The parent reports its own failure; the preview is dropped below so
        // the box returns to the last saved geometry.
      })
      .finally(() => {
        // Only if nothing newer (another drag, more key presses) replaced it.
        setLiveBox((current) => (current && current.id === id && current.box === box ? null : current));
      });
  }

  function flushKeyboardEdit() {
    const pending = pendingKeyboardRef.current;
    if (!pending) return;
    clearTimeout(pending.timer);
    pendingKeyboardRef.current = null;
    commitBox(pending.id, pending.box);
  }

  // ── Stage: pointer down on empty area → begin draw ─────────────────────────
  function onStageDown(e) {
    if (disabled || dragRef.current) return;
    capturePointer(e);
    const pos = getPos(e);
    setDrawStart(pos);
    setDraftBox({ x: pos.x, y: pos.y, width: 0.01, height: 0.01 });
  }

  // ── Hotspot body: pointer down → begin move ─────────────────────────────────
  function onHotspotDown(e, hotspot) {
    e.stopPropagation();
    if (disabled) return;
    flushKeyboardEdit();
    capturePointer(e);
    const pos = getPos(e);
    dragRef.current = { type: "move", id: hotspot.id, startPos: pos, originalBox: { ...hotspot.hotspot_box }, moved: false };
    setLiveBox({ id: hotspot.id, box: { ...hotspot.hotspot_box } });
  }

  // ── Corner handle: pointer down → begin resize ──────────────────────────────
  function onHandleDown(e, hotspot, handleId) {
    e.stopPropagation();
    if (disabled) return;
    flushKeyboardEdit();
    capturePointer(e);
    dragRef.current = { type: "resize", id: hotspot.id, corner: handleId, originalBox: { ...hotspot.hotspot_box } };
    setLiveBox({ id: hotspot.id, box: { ...hotspot.hotspot_box } });
  }

  // ── Pointer move ────────────────────────────────────────────────────────────
  function onMove(e) {
    const drag = dragRef.current;
    if (drag) {
      const pos = getPos(e);
      let newBox;
      if (drag.type === "move") {
        const dx = pos.x - drag.startPos.x;
        const dy = pos.y - drag.startPos.y;
        newBox = moveBox(drag.originalBox, dx, dy);
        drag.moved = drag.moved || Math.abs(dx) > 0.008 || Math.abs(dy) > 0.008;
      } else {
        newBox = applyResize(drag.originalBox, drag.corner, pos);
      }
      drag.box = newBox;
      setLiveBox({ id: drag.id, box: newBox });
      return;
    }
    if (!drawStart || disabled) return;
    setDraftBox(normalizeBox(drawStart, getPos(e)));
  }

  // ── Pointer up ──────────────────────────────────────────────────────────────
  function onUp(e) {
    const drag = dragRef.current;
    if (drag) {
      dragRef.current = null;
      if (drag.type === "move" && !drag.moved) {
        setLiveBox(null);
        onSelectHotspot?.(drag.id);
      } else {
        commitBox(drag.id, drag.box || drag.originalBox);
        onSelectHotspot?.(drag.id);
      }
      return;
    }
    if (!drawStart || disabled) return;
    const box = normalizeBox(drawStart, getPos(e));
    setDrawStart(null);
    setDraftBox(null);
    onCreateHotspot?.(box);
  }

  // ── Pointer cancel: the gesture was taken away (scroll, palm, system UI) ────
  // Abandon it. Committing here saved a move the person never finished.
  function onCancel() {
    if (dragRef.current) {
      dragRef.current = null;
      setLiveBox(null);
    }
    setDrawStart(null);
    setDraftBox(null);
  }

  // ── Keyboard path ───────────────────────────────────────────────────────────
  function onHotspotKeyDown(e, hotspot, isSelected, currentBox) {
    if (disabled) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelectHotspot?.(hotspot.id);
      return;
    }
    if (!isSelected || e.metaKey || e.ctrlKey || e.altKey) return;
    const next = nudgeBox(currentBox, e.key, { resize: e.shiftKey });
    if (!next) return;
    e.preventDefault(); // arrow keys must not scroll the page mid-edit
    if (pendingKeyboardRef.current) clearTimeout(pendingKeyboardRef.current.timer);
    pendingKeyboardRef.current = {
      id: hotspot.id,
      box: next,
      timer: setTimeout(flushKeyboardEdit, KEYBOARD_COMMIT_DELAY_MS),
    };
    setLiveBox({ id: hotspot.id, box: next });
  }

  const canEdit = !disabled && Boolean(image?.image_url);

  return (
    <div
      ref={stageRef}
      className="scan-stage touch-none select-none"
      style={{ aspectRatio: imageRatio || "4 / 3" }}
      role="group"
      aria-label="Room photo with item hotspots"
      onPointerDown={onStageDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onCancel}
    >
      {image?.image_url ? (
        <img
          src={image.image_url}
          alt={`Room scan ${image.position}`}
          className="h-full w-full object-cover pointer-events-none"
          draggable={false}
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            if (naturalWidth && naturalHeight) setImageRatio(`${naturalWidth} / ${naturalHeight}`);
          }}
        />
      ) : (
        /* Empty stage teaches the gesture: a ghost hotspot with a gold handle */
        <div className="relative flex h-full flex-col items-center justify-center gap-5 bg-[#16161a] px-8 text-center">
          <div className="relative h-[38%] w-[46%] rounded-xl border-2 border-dashed border-[#e17a9b]/35">
            <span className="absolute -bottom-1.5 -right-1.5 h-3.5 w-3.5 rounded-full border-2 border-[#16161a] bg-[#d9a441]" />
          </div>
          <div>
            <p className="text-[15px] font-semibold text-white/85">
              Upload room photos to start tagging
            </p>
            <p className="mt-1 text-[13px] text-white/45">
              You&apos;ll drag boxes like this one over each item worth rescuing
            </p>
          </div>
        </div>
      )}

      <div className="pointer-events-none absolute inset-0">
        {canEdit && (
          <div className="absolute right-4 top-4 flex items-center gap-2">
            {/* Hidden on narrow stages, where it would run into the controls
                the page overlays at the top-left. */}
            <div className="hidden rounded-full border border-white/40 bg-slate-950/72 px-3 py-2 text-[0.66rem] font-semibold uppercase tracking-[0.16em] text-white backdrop-blur-sm sm:block">
              Drag to tag · move &amp; resize existing
            </div>
            {/* Keyboard and switch users cannot drag: this creates the same
                default box a click does, centred, ready to be nudged. */}
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => onCreateHotspot?.(centeredDefaultBox(resolvedHotspots.length))}
              aria-describedby={keyboardHelpId}
              className="pointer-events-auto inline-flex items-center gap-1 rounded-full border border-white/40 bg-slate-950/72 px-3 py-2 text-[0.66rem] font-semibold uppercase tracking-[0.16em] text-white backdrop-blur-sm transition-colors hover:bg-slate-950/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
            >
              <Plus size={12} aria-hidden="true" />
              Add box
            </button>
          </div>
        )}
        <p id={keyboardHelpId} className="sr-only">
          Keyboard: Add box places a box in the centre of the photo. Focus a box and press Enter
          to select it, then use the arrow keys to move it and Shift plus the arrow keys to resize it.
        </p>

        {resolvedHotspots.map((hotspot, index) => {
          const box = liveBox?.id === hotspot.id ? liveBox.box : hotspot.hotspot_box;
          const isSelected = selectedHotspotId === hotspot.id;

          return (
            <React.Fragment key={hotspot.id}>
              {/* Hotspot box */}
              <motion.div
                initial={{ opacity: 0, scale: 0.96 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.18, delay: index * 0.04 }}
                role="button"
                tabIndex={disabled ? -1 : 0}
                aria-label={`Hotspot: ${hotspot.title || "Untitled item"}`}
                aria-pressed={isSelected}
                aria-describedby={keyboardHelpId}
                data-hotspot-id={hotspot.id}
                className={`pointer-events-auto absolute rounded-[1.25rem] border-2 transition-colors focus-visible:outline focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-white ${
                  isSelected
                    ? "border-slate-950 bg-[rgba(255,184,77,0.22)] shadow-[0_14px_26px_rgba(25,39,66,0.16)]"
                    : "border-white/80 bg-[rgba(11,122,114,0.18)] hover:bg-[rgba(11,122,114,0.26)]"
                }`}
                style={{
                  left: `${box.x * 100}%`,
                  top: `${box.y * 100}%`,
                  width: `${box.width * 100}%`,
                  height: `${box.height * 100}%`,
                  cursor: "move",
                }}
                onPointerDown={(e) => onHotspotDown(e, hotspot)}
                onKeyDown={(e) => onHotspotKeyDown(e, hotspot, isSelected, box)}
                onBlur={flushKeyboardEdit}
              >
                <span className="pointer-events-none absolute left-2 top-2 max-w-[90%] truncate rounded-full bg-slate-950/88 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white backdrop-blur-sm">
                  {hotspot.title}
                </span>
              </motion.div>

              {/* Corner resize handles, only on selected hotspot */}
              {isSelected && HANDLES.map((handle) => (
                <div
                  key={handle.id}
                  aria-hidden="true"
                  className="pointer-events-auto absolute z-20 h-4 w-4 rounded-full border-2 border-slate-900 bg-white shadow-lg transition-transform hover:scale-125"
                  style={{
                    left: `calc(${(box.x + handle.ax * box.width) * 100}% - 8px)`,
                    top: `calc(${(box.y + handle.ay * box.height) * 100}% - 8px)`,
                    cursor: handle.cursor,
                  }}
                  onPointerDown={(e) => onHandleDown(e, hotspot, handle.id)}
                />
              ))}
            </React.Fragment>
          );
        })}

        {/* In-progress draw box */}
        {draftBox && (
          <div
            className="pointer-events-none absolute rounded-[1.25rem] border-2 border-dashed border-slate-950 bg-[rgba(242,199,102,0.3)]"
            style={{
              left: `${draftBox.x * 100}%`,
              top: `${draftBox.y * 100}%`,
              width: `${draftBox.width * 100}%`,
              height: `${draftBox.height * 100}%`,
            }}
          />
        )}
      </div>
    </div>
  );
}
