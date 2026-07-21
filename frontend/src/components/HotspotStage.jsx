import { motion } from "framer-motion";
import React, { useRef, useState, useMemo } from "react";

function clamp(v, lo = 0, hi = 1) {
  return Math.min(hi, Math.max(lo, v));
}

function normalizeBox(start, end) {
  const minX = Math.min(start.x, end.x);
  const minY = Math.min(start.y, end.y);
  const w = Math.abs(end.x - start.x);
  const h = Math.abs(end.y - start.y);
  if (w < 0.02 || h < 0.02) {
    return { x: clamp(start.x - 0.09), y: clamp(start.y - 0.09), width: 0.18, height: 0.18 };
  }
  return { x: clamp(minX), y: clamp(minY), width: clamp(w, 0.02, 1), height: clamp(h, 0.02, 1) };
}

// Compute new box while dragging a corner handle
function applyResize(original, corner, pos) {
  const MIN = 0.05;
  let { x, y, width, height } = original;
  const right = x + width;
  const bottom = y + height;

  if (corner === "se") {
    width = clamp(pos.x - x, MIN, 1 - x);
    height = clamp(pos.y - y, MIN, 1 - y);
  } else if (corner === "sw") {
    x = clamp(pos.x, 0, right - MIN);
    width = right - x;
    height = clamp(pos.y - y, MIN, 1 - y);
  } else if (corner === "ne") {
    width = clamp(pos.x - x, MIN, 1 - x);
    y = clamp(pos.y, 0, bottom - MIN);
    height = bottom - y;
  } else if (corner === "nw") {
    x = clamp(pos.x, 0, right - MIN);
    y = clamp(pos.y, 0, bottom - MIN);
    width = right - x;
    height = bottom - y;
  }
  return { x, y, width, height };
}

// Corner handle descriptors: position as fraction of box (anchorX, anchorY) + CSS cursor
const HANDLES = [
  { id: "nw", ax: 0, ay: 0, cursor: "nw-resize" },
  { id: "ne", ax: 1, ay: 0, cursor: "ne-resize" },
  { id: "sw", ax: 0, ay: 1, cursor: "sw-resize" },
  { id: "se", ax: 1, ay: 1, cursor: "se-resize" },
];

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

  // Drawing state — for creating a brand-new hotspot
  const [drawStart, setDrawStart] = useState(null);
  const [draftBox, setDraftBox] = useState(null);

  // Drag state — for moving or resizing an existing hotspot
  // { type: "move"|"resize", id, corner?, startPos, originalBox, moved }
  const dragRef = useRef(null);
  const [liveBox, setLiveBox] = useState(null); // { id, box } live preview while dragging

  const resolvedHotspots = useMemo(() => hotspots || [], [hotspots]);

  function getPos(event) {
    const bounds = stageRef.current?.getBoundingClientRect();
    if (!bounds) return { x: 0, y: 0 };
    return {
      x: clamp((event.clientX - bounds.left) / bounds.width),
      y: clamp((event.clientY - bounds.top) / bounds.height),
    };
  }

  // ── Stage: pointer down on empty area → begin draw ─────────────────────────
  function onStageDown(e) {
    if (disabled || dragRef.current) return;
    stageRef.current?.setPointerCapture(e.pointerId);
    const pos = getPos(e);
    setDrawStart(pos);
    setDraftBox({ x: pos.x, y: pos.y, width: 0.01, height: 0.01 });
  }

  // ── Hotspot body: pointer down → begin move ─────────────────────────────────
  function onHotspotDown(e, hotspot) {
    e.stopPropagation();
    if (disabled) return;
    stageRef.current?.setPointerCapture(e.pointerId);
    const pos = getPos(e);
    dragRef.current = { type: "move", id: hotspot.id, startPos: pos, originalBox: { ...hotspot.hotspot_box }, moved: false };
    setLiveBox({ id: hotspot.id, box: { ...hotspot.hotspot_box } });
  }

  // ── Corner handle: pointer down → begin resize ──────────────────────────────
  function onHandleDown(e, hotspot, handleId) {
    e.stopPropagation();
    if (disabled) return;
    stageRef.current?.setPointerCapture(e.pointerId);
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
        const b = drag.originalBox;
        newBox = {
          x: clamp(b.x + dx, 0, 1 - b.width),
          y: clamp(b.y + dy, 0, 1 - b.height),
          width: b.width,
          height: b.height,
        };
        drag.moved = Math.abs(dx) > 0.008 || Math.abs(dy) > 0.008;
      } else {
        newBox = applyResize(drag.originalBox, drag.corner, pos);
      }
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
      const finalBox = liveBox?.box || drag.originalBox;
      dragRef.current = null;
      setLiveBox(null);
      if (drag.type === "move" && !drag.moved) {
        onSelectHotspot?.(drag.id);
      } else {
        onUpdateHotspot?.(drag.id, finalBox);
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

  return (
    <div
      ref={stageRef}
      className="scan-stage aspect-[4/3] touch-none select-none"
      onPointerDown={onStageDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      {image?.image_url ? (
        <img
          src={image.image_url}
          alt={`Room scan ${image.position}`}
          className="h-full w-full object-cover pointer-events-none"
          draggable={false}
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
        {!disabled && image?.image_url && (
          <div className="absolute right-4 top-4 rounded-full border border-white/40 bg-slate-950/72 px-3 py-2 text-[0.66rem] font-semibold uppercase tracking-[0.16em] text-white backdrop-blur-sm">
            Drag to tag · move &amp; resize existing
          </div>
        )}

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
                className={`pointer-events-auto absolute rounded-[1.25rem] border-2 transition-colors ${
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
              >
                <span className="pointer-events-none absolute left-2 top-2 max-w-[90%] truncate rounded-full bg-slate-950/88 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white backdrop-blur-sm">
                  {hotspot.title}
                </span>
              </motion.div>

              {/* Corner resize handles — only on selected hotspot */}
              {isSelected && HANDLES.map((handle) => (
                <div
                  key={handle.id}
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
